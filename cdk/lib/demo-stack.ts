import * as cdk from "aws-cdk-lib";
import { KubectlV32Layer } from "@aws-cdk/lambda-layer-kubectl-v32";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecr from "aws-cdk-lib/aws-ecr";
import * as eks from "aws-cdk-lib/aws-eks";
import * as iam from "aws-cdk-lib/aws-iam";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import { Construct } from "constructs";

export interface DemoStackProps extends cdk.StackProps {
  clusterName: string;
  demoMode: boolean;
  enableRds: boolean;
  enableElastiCache: boolean;
  nodeInstanceType: string;
  nodeMinSize: number;
  nodeMaxSize: number;
  nodeDesiredSize: number;
}

export class DemoStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: DemoStackProps) {
    super(scope, id, props);

    const {
      clusterName,
      demoMode,
      enableRds,
      nodeInstanceType,
      nodeMinSize,
      nodeMaxSize,
      nodeDesiredSize,
    } = props;

    // ── VPC ───────────────────────────────────────────────────────────────────
    const vpc = new ec2.Vpc(this, "Vpc", {
      vpcName: `${clusterName}-vpc`,
      maxAzs: 3,
      natGateways: 1,
      subnetConfiguration: [
        {
          name: "Public",
          subnetType: ec2.SubnetType.PUBLIC,
          cidrMask: 20,
        },
        {
          name: "Private",
          subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
          cidrMask: 20,
        },
      ],
    });

    // Tag subnets for EKS / ALB
    vpc.publicSubnets.forEach((subnet) => {
      cdk.Tags.of(subnet).add("kubernetes.io/role/elb", "1");
    });
    vpc.privateSubnets.forEach((subnet) => {
      cdk.Tags.of(subnet).add("kubernetes.io/role/internal-elb", "1");
    });

    // ── EKS ───────────────────────────────────────────────────────────────────
    const cluster = new eks.Cluster(this, "Cluster", {
      clusterName,
      vpc,
      version: eks.KubernetesVersion.V1_32,
      defaultCapacity: 0,
      endpointAccess: eks.EndpointAccess.PUBLIC,
      kubectlLayer: new KubectlV32Layer(this, "KubectlLayer"),
      authenticationMode: eks.AuthenticationMode.API_AND_CONFIG_MAP,
    });

    const adminPrincipalArn = this.node.tryGetContext("adminPrincipalArn") as
      | string
      | undefined;
    if (adminPrincipalArn) {
      cluster.grantAccess("AdminAccess", adminPrincipalArn, [
        eks.AccessPolicy.fromAccessPolicyName("AmazonEKSClusterAdminPolicy", {
          accessScopeType: eks.AccessScopeType.CLUSTER,
        }),
      ]);
    }

    const nodeGroup = new eks.Nodegroup(this, "GeneralNodeGroup", {
      cluster,
      nodegroupName: "general",
      instanceTypes: [new ec2.InstanceType(nodeInstanceType)],
      minSize: nodeMinSize,
      maxSize: nodeMaxSize,
      desiredSize: nodeDesiredSize,
      subnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      labels: { role: "general" },
    });

    // EBS CSI driver installed in bootstrap.sh to avoid SA conflict with CDK kubectl handler

    // ── S3 document store ───────────────────────────────────────────────────
    const documentsBucket = new s3.Bucket(this, "DocumentsBucket", {
      bucketName: `${clusterName}-documents-${this.account}`,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      versioned: true,
      removalPolicy: demoMode ? cdk.RemovalPolicy.DESTROY : cdk.RemovalPolicy.RETAIN,
      autoDeleteObjects: demoMode,
    });

    // ── ECR repositories ──────────────────────────────────────────────────────
    const ecrRepos = ["agents", "ingestion", "ui"].map((name) =>
      new ecr.Repository(this, `Ecr${name}`, {
        repositoryName: `${clusterName}/${name}`,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
        emptyOnDelete: true,
      }),
    );

    // ── IRSA: Bedrock + S3 for agent and ingestion pods ───────────────────────
    // Namespaces must exist before CDK applies ServiceAccount manifests
    const appNamespaces = cluster.addManifest(
      "AppNamespaces",
      {
        apiVersion: "v1",
        kind: "Namespace",
        metadata: { name: "agents" },
      },
      {
        apiVersion: "v1",
        kind: "Namespace",
        metadata: { name: "rag" },
      },
    );

    const agentsSa = cluster.addServiceAccount("AgentsSa", {
      name: "research-agents",
      namespace: "agents",
    });
    agentsSa.node.addDependency(appNamespaces);

    const ingestionSa = cluster.addServiceAccount("IngestionSa", {
      name: "ingestion",
      namespace: "rag",
    });
    ingestionSa.node.addDependency(appNamespaces);

    for (const sa of [agentsSa, ingestionSa]) {
      sa.addToPrincipalPolicy(
        new iam.PolicyStatement({
          actions: [
            "bedrock:InvokeModel",
            "bedrock:InvokeModelWithResponseStream",
          ],
          resources: ["*"],
        }),
      );
      documentsBucket.grantReadWrite(sa);
    }

    // ── Secrets Manager ───────────────────────────────────────────────────────
    const appSecret = new secretsmanager.Secret(this, "AppSecret", {
      secretName: `${clusterName}/app-secrets`,
      description: "LangGraph demo application secrets",
      secretObjectValue: {
        postgres_url: cdk.SecretValue.unsafePlainText(""),
        redis_url: cdk.SecretValue.unsafePlainText(
          "redis://redis.rag.svc.cluster.local:6379",
        ),
        langsmith_api_key: cdk.SecretValue.unsafePlainText(
          process.env.LANGSMITH_API_KEY ?? "",
        ),
        openai_api_key: cdk.SecretValue.unsafePlainText(
          process.env.OPENAI_API_KEY ?? "",
        ),
      },
    });

    // ── Stack outputs (consumed by deploy scripts) ────────────────────────────
    new cdk.CfnOutput(this, "OutClusterName", {
      value: cluster.clusterName,
      exportName: `${clusterName}-ClusterName`,
    });

    new cdk.CfnOutput(this, "OutNodeGroupName", {
      value: nodeGroup.nodegroupName,
      exportName: `${clusterName}-NodeGroupName`,
    });

    new cdk.CfnOutput(this, "OutNodeMaxSize", {
      value: nodeMaxSize.toString(),
    });

    new cdk.CfnOutput(this, "OutDemoMode", {
      value: demoMode.toString(),
    });

    new cdk.CfnOutput(this, "OutAgentsIrsaRoleArn", {
      value: agentsSa.role.roleArn,
      exportName: `${clusterName}-AgentsIrsaRoleArn`,
    });

    new cdk.CfnOutput(this, "OutIngestionIrsaRoleArn", {
      value: ingestionSa.role.roleArn,
      exportName: `${clusterName}-IngestionIrsaRoleArn`,
    });

    new cdk.CfnOutput(this, "OutDocumentsBucket", {
      value: documentsBucket.bucketName,
      exportName: `${clusterName}-DocumentsBucket`,
    });

    new cdk.CfnOutput(this, "OutSecretsManagerArn", {
      value: appSecret.secretArn,
    });

    new cdk.CfnOutput(this, "OutEcrRepositories", {
      value: ecrRepos.map((r) => r.repositoryUri).join(","),
    });

    new cdk.CfnOutput(this, "OutKubeconfigCommand", {
      value: `aws eks update-kubeconfig --region ${this.region} --name ${cluster.clusterName}`,
    });

    new cdk.CfnOutput(this, "OutRdsInstanceId", {
      value: enableRds ? `${clusterName}-postgres` : "",
      description: "Empty when RDS disabled (demo default)",
    });

    if (enableRds) {
      cdk.Annotations.of(this).addWarning(
        "RDS is enabled but not yet implemented in CDK stack — set enableRds=false for demo.",
      );
    }
  }
}
