import * as budgets from "aws-cdk-lib/aws-budgets";
import * as cdk from "aws-cdk-lib";
import { KubectlV32Layer } from "@aws-cdk/lambda-layer-kubectl-v32";
import * as acm from "aws-cdk-lib/aws-certificatemanager";
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecr from "aws-cdk-lib/aws-ecr";
import * as eks from "aws-cdk-lib/aws-eks";
import * as iam from "aws-cdk-lib/aws-iam";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import { Construct } from "constructs";
import { applyProjectCostTags, tagComponent } from "./cost-tags";

export interface DemoStackProps extends cdk.StackProps {
  clusterName: string;
  environment: string;
  costCenter?: string;
  costOwner?: string;
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
      environment,
      costCenter,
      costOwner,
      demoMode,
      enableRds,
      nodeInstanceType,
      nodeMinSize,
      nodeMaxSize,
      nodeDesiredSize,
    } = props;

    applyProjectCostTags(this, {
      project: clusterName,
      environment,
      workload: "research-showcase",
      costCenter,
      owner: costOwner,
    });

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
    tagComponent(vpc, "networking");

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
    tagComponent(cluster, "eks");

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
    tagComponent(nodeGroup, "compute");

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
    tagComponent(documentsBucket, "storage");

    // ── ECR repositories ──────────────────────────────────────────────────────
    const ecrRepos = ["agents", "ingestion", "ui"].map((name) => {
      const repo = new ecr.Repository(this, `Ecr${name}`, {
        repositoryName: `${clusterName}/${name}`,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
        emptyOnDelete: true,
      });
      tagComponent(repo, "storage");
      return repo;
    });

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

    const defaultLlmModel =
      (this.node.tryGetContext("defaultLlmModel") as string | undefined) ??
      "us.anthropic.claude-sonnet-4-6";
    const defaultEmbeddingModel =
      (this.node.tryGetContext("defaultEmbeddingModel") as string | undefined) ??
      "amazon.titan-embed-text-v2:0";
    const cragGraderModel =
      (this.node.tryGetContext("cragGraderModel") as string | undefined) ??
      "us.anthropic.claude-3-haiku-20240307-v1:0";

    const bedrockInvokeResources = (modelId: string): string[] => {
      if (modelId.startsWith("us.") || modelId.startsWith("global.")) {
        const foundationModelId = modelId.replace(/^(?:us|global)\./, "");
        return [
          `arn:aws:bedrock:${this.region}:${this.account}:inference-profile/${modelId}`,
          `arn:aws:bedrock:*::foundation-model/${foundationModelId}`,
        ];
      }
      return [`arn:aws:bedrock:${this.region}::foundation-model/${modelId}`];
    };

    agentsSa.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
        ],
        resources: [
          ...bedrockInvokeResources(defaultLlmModel),
          ...bedrockInvokeResources(defaultEmbeddingModel),
        ],
      }),
    );
    documentsBucket.grantReadWrite(agentsSa);

    ingestionSa.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: ["bedrock:InvokeModel"],
        resources: [
          ...bedrockInvokeResources(defaultEmbeddingModel),
          ...bedrockInvokeResources(cragGraderModel),
        ],
      }),
    );
    documentsBucket.grantReadWrite(ingestionSa);

    // ── Cognito (showcase UI auth) ────────────────────────────────────────────
    const cognitoDomainPrefix =
      (this.node.tryGetContext("cognitoDomainPrefix") as string | undefined) ??
      `${clusterName.replace(/[^a-z0-9-]/gi, "-").toLowerCase()}-demo`;
    const appDomainName = this.node.tryGetContext("appDomainName") as
      | string
      | undefined;
    const certificateArn = this.node.tryGetContext("certificateArn") as
      | string
      | undefined;
    const ingressEnabled = Boolean(
      appDomainName && (certificateArn || appDomainName),
    );

    const showcaseUserPool = new cognito.UserPool(this, "ShowcaseUserPool", {
      userPoolName: `${clusterName}-showcase`,
      selfSignUpEnabled: false,
      signInAliases: { email: true },
      autoVerify: { email: true },
      standardAttributes: {
        email: { required: true, mutable: true },
      },
      passwordPolicy: {
        minLength: 12,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: false,
      },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      removalPolicy: demoMode
        ? cdk.RemovalPolicy.DESTROY
        : cdk.RemovalPolicy.RETAIN,
    });
    tagComponent(showcaseUserPool, "auth");

    const showcaseClient = showcaseUserPool.addClient("ShowcaseWebClient", {
      userPoolClientName: "showcase-web",
      // ALB authenticate-cognito requires a confidential client (with secret).
      generateSecret: Boolean(appDomainName),
      authFlows: {
        userPassword: true,
        userSrp: true,
      },
      ...(appDomainName
        ? {
            oAuth: {
              flows: { authorizationCodeGrant: true },
              scopes: [
                cognito.OAuthScope.OPENID,
                cognito.OAuthScope.EMAIL,
                cognito.OAuthScope.PROFILE,
              ],
              callbackUrls: [
                `https://${appDomainName}/oauth2/idpresponse`,
              ],
              logoutUrls: [`https://${appDomainName}/`],
            },
          }
        : {}),
    });

    showcaseUserPool.addDomain("ShowcaseDomain", {
      cognitoDomain: {
        domainPrefix: cognitoDomainPrefix,
      },
    });

    let ingressCertificateArn = certificateArn ?? "";
    if (appDomainName && !certificateArn) {
      const cert = new acm.Certificate(this, "ShowcaseIngressCert", {
        domainName: appDomainName,
        validation: acm.CertificateValidation.fromDns(),
      });
      ingressCertificateArn = cert.certificateArn;
      tagComponent(cert, "networking");
    }

    const cognitoHostedDomain = `${cognitoDomainPrefix}.auth.${this.region}.amazoncognito.com`;

    const budgetAlertEmail = this.node.tryGetContext("budgetAlertEmail") as
      | string
      | undefined;
    const bedrockBudgetUsd =
      (this.node.tryGetContext("bedrockBudgetUsd") as number | undefined) ?? 50;

    if (budgetAlertEmail) {
      const bedrockBudget = new budgets.CfnBudget(this, "BedrockBudget", {
        budget: {
          budgetName: `${clusterName}-bedrock-monthly`,
          budgetType: "COST",
          timeUnit: "MONTHLY",
          budgetLimit: {
            amount: bedrockBudgetUsd,
            unit: "USD",
          },
          costFilters: {
            Service: ["Amazon Bedrock"],
          },
        },
        notificationsWithSubscribers: [
          {
            notification: {
              notificationType: "ACTUAL",
              comparisonOperator: "GREATER_THAN",
              threshold: 80,
              thresholdType: "PERCENTAGE",
            },
            subscribers: [
              {
                subscriptionType: "EMAIL",
                address: budgetAlertEmail,
              },
            ],
          },
          {
            notification: {
              notificationType: "FORECASTED",
              comparisonOperator: "GREATER_THAN",
              threshold: 100,
              thresholdType: "PERCENTAGE",
            },
            subscribers: [
              {
                subscriptionType: "EMAIL",
                address: budgetAlertEmail,
              },
            ],
          },
        ],
      });
      tagComponent(bedrockBudget, "ml-bedrock");
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
        tavily_api_key: cdk.SecretValue.unsafePlainText(
          process.env.TAVILY_API_KEY ?? "",
        ),
      },
    });
    tagComponent(appSecret, "secrets");

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

    new cdk.CfnOutput(this, "OutCognitoUserPoolId", {
      value: showcaseUserPool.userPoolId,
      exportName: `${clusterName}-CognitoUserPoolId`,
    });

    new cdk.CfnOutput(this, "OutCognitoUserPoolArn", {
      value: showcaseUserPool.userPoolArn,
      exportName: `${clusterName}-CognitoUserPoolArn`,
    });

    new cdk.CfnOutput(this, "OutCognitoClientId", {
      value: showcaseClient.userPoolClientId,
      exportName: `${clusterName}-CognitoClientId`,
    });

    if (appDomainName) {
      new cdk.CfnOutput(this, "OutCognitoClientSecret", {
        value: showcaseClient.userPoolClientSecret.unsafeUnwrap(),
        description: "Required for ALB authenticate-cognito (do not commit)",
      });
    }

    new cdk.CfnOutput(this, "OutCognitoDomainPrefix", {
      value: cognitoDomainPrefix,
    });

    new cdk.CfnOutput(this, "OutCognitoHostedUiUrl", {
      value: `https://${cognitoHostedDomain}`,
    });

    new cdk.CfnOutput(this, "OutAppDomainName", {
      value: appDomainName ?? "",
      description: "Set in cdk.json to enable HTTPS ALB + Cognito ingress",
    });

    new cdk.CfnOutput(this, "OutIngressCertificateArn", {
      value: ingressCertificateArn,
      description: "ACM cert ARN for ALB (auto-created or from certificateArn context)",
    });

    new cdk.CfnOutput(this, "OutIngressEnabled", {
      value: ingressEnabled && ingressCertificateArn ? "true" : "false",
    });

    new cdk.CfnOutput(this, "OutDefaultLlmModel", {
      value: defaultLlmModel,
    });

    new cdk.CfnOutput(this, "OutDefaultEmbeddingModel", {
      value: defaultEmbeddingModel,
    });

    if (budgetAlertEmail) {
      new cdk.CfnOutput(this, "OutBedrockBudgetEmail", {
        value: budgetAlertEmail,
        description: "Email receiving Bedrock spend alerts at 80% actual / 100% forecast",
      });
    }

    if (enableRds) {
      cdk.Annotations.of(this).addWarning(
        "RDS is enabled but not yet implemented in CDK stack — set enableRds=false for demo.",
      );
    }
  }
}
