import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecr_assets from "aws-cdk-lib/aws-ecr-assets";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as efs from "aws-cdk-lib/aws-efs";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import * as apigwv2 from "aws-cdk-lib/aws-apigatewayv2";
import * as apigwAuthorizers from "aws-cdk-lib/aws-apigatewayv2-authorizers";
import * as apigwIntegrations from "aws-cdk-lib/aws-apigatewayv2-integrations";
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as events from "aws-cdk-lib/aws-events";
import * as targets from "aws-cdk-lib/aws-events-targets";
import { Construct } from "constructs";
import * as path from "path";

export interface CleanRagStackProps extends cdk.StackProps {
  projectName?: string;
}

export class CleanRagStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: CleanRagStackProps = {}) {
    super(scope, id, props);

    const projectName = props.projectName ?? "langrag-clean";
    const langsmithApiKey = process.env.LANGSMITH_API_KEY ?? "";
    const langsmithProject = process.env.LANGSMITH_PROJECT ?? "rag-demo";
    // Stable hostname for ingestion (outside VPC) → Qdrant. Not advertised on the public architecture page.
    const qdrantDnsName = "qdrant-demo.automatico.llc";
    const hostedZoneId = "Z03675412E5MU377HS9OE";

    const docsBucket = new s3.Bucket(this, "DocsBucket", {
      bucketName: undefined,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      autoDeleteObjects: false,
    });
    cdk.Tags.of(docsBucket).add("langrag:role", "corpus");
    cdk.Tags.of(docsBucket).add("langrag:state", "parked-keep");

    new cdk.CfnOutput(this, "DocsBucketName", { value: docsBucket.bucketName });

    const paused =
      this.node.tryGetContext("paused") === true ||
      this.node.tryGetContext("paused") === "true";
    if (paused) {
      // Keep the corpus bucket in-stack. Runtime (Cognito, API, Lambdas, Fargate, EFS)
      // is removed so a later deploy without -c paused=true can reattach to this bucket.
      return;
    }

    const checkpointTable = new dynamodb.Table(this, "CheckpointTable", {
      tableName: `${projectName}-checkpoints`,
      partitionKey: { name: "PK", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "SK", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // No NAT Gateway (demo cost). Qdrant runs with a task public IP; ingestion Lambda
    // stays outside the VPC so Bedrock/S3/LangSmith work without NAT.
    const vpc = new ec2.Vpc(this, "Vpc", {
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [
        { name: "Public", subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 },
        {
          name: "Private",
          subnetType: ec2.SubnetType.PRIVATE_ISOLATED,
          cidrMask: 24,
        },
      ],
    });

    const cluster = new ecs.Cluster(this, "Cluster", {
      vpc,
      clusterName: `${projectName}-cluster`,
    });

    const fileSystem = new efs.FileSystem(this, "QdrantEfs", {
      vpc,
      encrypted: true,
      lifecyclePolicy: efs.LifecyclePolicy.AFTER_14_DAYS,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    const qdrantApiKey = new secretsmanager.Secret(this, "QdrantApiKey", {
      description: "Qdrant API key for langrag-clean demo",
      generateSecretString: {
        passwordLength: 40,
        excludePunctuation: true,
      },
    });

    const qdrantSg = new ec2.SecurityGroup(this, "QdrantSg", {
      vpc,
      description: "Qdrant Fargate",
      allowAllOutbound: true,
    });
    // Ingestion Lambda is outside the VPC; API key is the gate (Cognito still fronts the app APIs).
    qdrantSg.addIngressRule(
      ec2.Peer.anyIpv4(),
      ec2.Port.tcp(6333),
      "Qdrant API (key required)",
    );

    const qdrantTaskDef = new ecs.FargateTaskDefinition(this, "QdrantTask", {
      cpu: 512,
      memoryLimitMiB: 1024,
    });

    qdrantTaskDef.addVolume({
      name: "qdrant-storage",
      efsVolumeConfiguration: {
        fileSystemId: fileSystem.fileSystemId,
        transitEncryption: "ENABLED",
      },
    });

    const qdrantContainer = qdrantTaskDef.addContainer("Qdrant", {
      image: ecs.ContainerImage.fromRegistry("qdrant/qdrant:v1.12.5"),
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: "qdrant",
        logRetention: logs.RetentionDays.ONE_WEEK,
      }),
      portMappings: [{ containerPort: 6333 }],
      secrets: {
        QDRANT__SERVICE__API_KEY: ecs.Secret.fromSecretsManager(qdrantApiKey),
      },
    });

    qdrantContainer.addMountPoints({
      containerPath: "/qdrant/storage",
      sourceVolume: "qdrant-storage",
      readOnly: false,
    });

    const qdrantService = new ecs.FargateService(this, "QdrantService", {
      cluster,
      taskDefinition: qdrantTaskDef,
      desiredCount: 1,
      assignPublicIp: true,
      securityGroups: [qdrantSg],
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      // One Qdrant only — EFS/RocksDB cannot be opened by two tasks.
      // AZ rebalancing requires maxPercent > 100, which starts a second task.
      availabilityZoneRebalancing: ecs.AvailabilityZoneRebalancing.DISABLED,
      minHealthyPercent: 0,
      maxHealthyPercent: 100,
      circuitBreaker: { rollback: true },
      cloudMapOptions: {
        name: "qdrant",
        cloudMapNamespace: cluster.addDefaultCloudMapNamespace({
          name: `${projectName}.local`,
        }),
      },
    });

    fileSystem.connections.allowDefaultPortFrom(qdrantService);

    const qdrantUrl = `http://${qdrantDnsName}:6333`;

    const dnsUpdater = new lambda.Function(this, "QdrantDnsUpdater", {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "index.handler",
      timeout: cdk.Duration.seconds(60),
      memorySize: 128,
      description: "Keeps qdrant-demo.automatico.llc A record on Qdrant task start",
      environment: {
        HOSTED_ZONE_ID: hostedZoneId,
        RECORD_NAME: qdrantDnsName,
        CLUSTER_ARN: cluster.clusterArn,
      },
      code: lambda.Code.fromInline(`
import boto3, os
ecs, ec2, r53 = boto3.client("ecs"), boto3.client("ec2"), boto3.client("route53")
ZONE, NAME, CLUSTER = os.environ["HOSTED_ZONE_ID"], os.environ["RECORD_NAME"], os.environ["CLUSTER_ARN"]

def handler(event, context):
    detail = event.get("detail") or {}
    if detail.get("lastStatus") != "RUNNING":
        return {"ok": True, "skip": "not-running"}
    if detail.get("clusterArn") != CLUSTER:
        return {"ok": True, "skip": "other-cluster"}
    task_arn = detail.get("taskArn")
    if not task_arn:
        return {"ok": False, "error": "no-task"}
    task = ecs.describe_tasks(cluster=CLUSTER, tasks=[task_arn])["tasks"][0]
    enis = []
    for att in task.get("attachments") or []:
        for d in att.get("details") or []:
            if d.get("name") == "networkInterfaceId":
                enis.append(d["value"])
    if not enis:
        return {"ok": False, "error": "no-eni"}
    ni = ec2.describe_network_interfaces(NetworkInterfaceIds=enis)["NetworkInterfaces"][0]
    ip = (ni.get("Association") or {}).get("PublicIp")
    if not ip:
        return {"ok": False, "error": "no-public-ip"}
    r53.change_resource_record_sets(
        HostedZoneId=ZONE,
        ChangeBatch={"Changes": [{
            "Action": "UPSERT",
            "ResourceRecordSet": {
                "Name": NAME,
                "Type": "A",
                "TTL": 60,
                "ResourceRecords": [{"Value": ip}],
            },
        }]},
    )
    return {"ok": True, "ip": ip}
`),
    });
    dnsUpdater.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["ecs:DescribeTasks"],
        resources: ["*"],
      }),
    );
    dnsUpdater.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["ec2:DescribeNetworkInterfaces"],
        resources: ["*"],
      }),
    );
    dnsUpdater.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["route53:ChangeResourceRecordSets"],
        resources: [`arn:aws:route53:::hostedzone/${hostedZoneId}`],
      }),
    );
    new events.Rule(this, "QdrantTaskRunningDns", {
      description: "Update qdrant-demo DNS when Qdrant task becomes RUNNING",
      eventPattern: {
        source: ["aws.ecs"],
        detailType: ["ECS Task State Change"],
        detail: {
          clusterArn: [cluster.clusterArn],
          lastStatus: ["RUNNING"],
          desiredStatus: ["RUNNING"],
        },
      },
      targets: [new targets.LambdaFunction(dnsUpdater)],
    });

    const ingestionRole = new iam.Role(this, "IngestionLambdaRole", {
      assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName(
          "service-role/AWSLambdaBasicExecutionRole",
        ),
      ],
    });
    docsBucket.grantReadWrite(ingestionRole);
    qdrantApiKey.grantRead(ingestionRole);
    ingestionRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ["bedrock:InvokeModel", "bedrock:Rerank"],
        resources: ["*"],
      }),
    );

    const repoRoot = path.join(__dirname, "..", "..");

    const ingestionFn = new lambda.DockerImageFunction(this, "IngestionFn", {
      code: lambda.DockerImageCode.fromImageAsset(
        path.join(repoRoot, "ingestion"),
        {
          file: "Dockerfile.lambda",
          platform: ecr_assets.Platform.LINUX_AMD64,
        },
      ),
      timeout: cdk.Duration.minutes(5),
      memorySize: 2048,
      environment: {
        S3_DOCUMENT_BUCKET: docsBucket.bucketName,
        QDRANT_URL: qdrantUrl,
        QDRANT_API_KEY: qdrantApiKey.secretValue.unsafeUnwrap(),
        QDRANT_COLLECTION: "research_docs",
        BEDROCK_REGION: cdk.Stack.of(this).region,
        LLM_PROVIDER: "bedrock",
        RAG_HYBRID: "true",
        RAG_RERANK: "true",
        RERANK_MODEL_ID: "amazon.rerank-v1:0",
        RERANK_REGION: "us-west-2",
        RAG_PHRASE_CANDIDATE_BACKFILL: "true",
        RAG_MIN_VECTOR_SCORE_PROSE: "0.12",
        RAG_MIN_VECTOR_SCORE_MARKDOWN: "0.10",
        RAG_MIN_VECTOR_SCORE_CODE: "0.08",
        RAG_MIN_VECTOR_SCORE_TABLE: "0.10",
      },
      role: ingestionRole,
    });

    if (langsmithApiKey) {
      ingestionFn.addEnvironment("LANGSMITH_API_KEY", langsmithApiKey);
      ingestionFn.addEnvironment("LANGSMITH_PROJECT", langsmithProject);
      ingestionFn.addEnvironment("LANGSMITH_TRACING", "true");
      ingestionFn.addEnvironment("LANGCHAIN_TRACING_V2", "true");
      ingestionFn.addEnvironment("LANGCHAIN_API_KEY", langsmithApiKey);
      ingestionFn.addEnvironment("LANGCHAIN_PROJECT", langsmithProject);
      ingestionFn.addEnvironment("LANGCHAIN_CALLBACKS_BACKGROUND", "false");
    }

    new events.Rule(this, "NightlyEval", {
      schedule: events.Schedule.cron({ minute: "0", hour: "12" }),
      description: "Nightly retrieval smoke eval",
      targets: [
        new targets.LambdaFunction(ingestionFn, {
          event: events.RuleTargetInput.fromObject({ job: "eval" }),
        }),
      ],
    });

    const agentsRole = new iam.Role(this, "AgentsLambdaRole", {
      assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
    });
    checkpointTable.grantReadWriteData(agentsRole);
    agentsRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ["bedrock:InvokeModel", "bedrock:Converse"],
        resources: ["*"],
      }),
    );

    const agentsFn = new lambda.DockerImageFunction(this, "AgentsFn", {
      code: lambda.DockerImageCode.fromImageAsset(
        path.join(repoRoot, "agents"),
        {
          file: "Dockerfile.lambda",
          platform: ecr_assets.Platform.LINUX_AMD64,
        },
      ),
      timeout: cdk.Duration.minutes(15),
      memorySize: 2048,
      environment: {
        CHECKPOINT_TABLE: checkpointTable.tableName,
        USE_DYNAMODB_CHECKPOINTER: "true",
        BEDROCK_REGION: cdk.Stack.of(this).region,
        DEFAULT_LLM_MODEL: "us.amazon.nova-lite-v1:0",
        LLM_PROVIDER: "bedrock",
        RAG_TOP_K: "5",
        RAG_CORRECTIVE: "true",
      },
      role: agentsRole,
    });

    if (langsmithApiKey) {
      agentsFn.addEnvironment("LANGSMITH_API_KEY", langsmithApiKey);
      agentsFn.addEnvironment("LANGSMITH_PROJECT", langsmithProject);
      agentsFn.addEnvironment("LANGSMITH_TRACING", "true");
      agentsFn.addEnvironment("LANGCHAIN_TRACING_V2", "true");
      agentsFn.addEnvironment("LANGCHAIN_API_KEY", langsmithApiKey);
      agentsFn.addEnvironment("LANGCHAIN_PROJECT", langsmithProject);
      agentsFn.addEnvironment("LANGCHAIN_CALLBACKS_BACKGROUND", "false");
    }

    const userPool = new cognito.UserPool(this, "UserPool", {
      userPoolName: `${projectName}-users`,
      selfSignUpEnabled: false,
      signInAliases: { email: true, username: true },
      passwordPolicy: {
        minLength: 12,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: true,
      },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    const userPoolClient = userPool.addClient("UiClient", {
      authFlows: {
        userPassword: true,
        userSrp: true,
      },
      preventUserExistenceErrors: true,
      accessTokenValidity: cdk.Duration.hours(1),
      idTokenValidity: cdk.Duration.hours(1),
      refreshTokenValidity: cdk.Duration.days(30),
    });

    const jwtAuthorizer = new apigwAuthorizers.HttpJwtAuthorizer(
      "JwtAuth",
      `https://cognito-idp.${this.region}.amazonaws.com/${userPool.userPoolId}`,
      {
        jwtAudience: [userPoolClient.userPoolClientId],
      },
    );

    const httpApi = new apigwv2.HttpApi(this, "HttpApi", {
      apiName: `${projectName}-api`,
      corsPreflight: {
        allowHeaders: ["authorization", "content-type", "*"],
        allowMethods: [
          apigwv2.CorsHttpMethod.GET,
          apigwv2.CorsHttpMethod.POST,
          apigwv2.CorsHttpMethod.DELETE,
        ],
        allowOrigins: ["*"],
      },
    });

    agentsFn.addEnvironment("INGESTION_API_URL", httpApi.apiEndpoint!);
    agentsFn.addEnvironment("INGESTION_FUNCTION_NAME", ingestionFn.functionName);
    ingestionFn.grantInvoke(agentsRole);

    httpApi.addRoutes({
      path: "/ingest/{proxy+}",
      methods: [apigwv2.HttpMethod.ANY],
      integration: new apigwIntegrations.HttpLambdaIntegration(
        "IngestionIntegration",
        ingestionFn,
      ),
      authorizer: jwtAuthorizer,
    });

    httpApi.addRoutes({
      path: "/rag/{proxy+}",
      methods: [apigwv2.HttpMethod.ANY],
      integration: new apigwIntegrations.HttpLambdaIntegration(
        "RagIntegration",
        ingestionFn,
      ),
      authorizer: jwtAuthorizer,
    });

    httpApi.addRoutes({
      path: "/health",
      methods: [apigwv2.HttpMethod.GET],
      integration: new apigwIntegrations.HttpLambdaIntegration(
        "HealthIntegration",
        ingestionFn,
      ),
    });

    httpApi.addRoutes({
      path: "/ask",
      methods: [apigwv2.HttpMethod.POST],
      integration: new apigwIntegrations.HttpLambdaIntegration(
        "AskIntegration",
        agentsFn,
      ),
      authorizer: jwtAuthorizer,
    });

    httpApi.addRoutes({
      path: "/resume",
      methods: [apigwv2.HttpMethod.POST],
      integration: new apigwIntegrations.HttpLambdaIntegration(
        "ResumeIntegration",
        agentsFn,
      ),
      authorizer: jwtAuthorizer,
    });

    new cdk.CfnOutput(this, "ApiEndpoint", { value: httpApi.apiEndpoint! });
    new cdk.CfnOutput(this, "UserPoolId", { value: userPool.userPoolId });
    new cdk.CfnOutput(this, "UserPoolClientId", {
      value: userPoolClient.userPoolClientId,
    });
    new cdk.CfnOutput(this, "UserPoolRegion", { value: this.region });
    new cdk.CfnOutput(this, "CheckpointTableName", {
      value: checkpointTable.tableName,
    });
    new cdk.CfnOutput(this, "QdrantUrl", { value: qdrantUrl });
  }
}
