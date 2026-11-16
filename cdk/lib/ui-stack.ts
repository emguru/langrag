import * as cdk from "aws-cdk-lib";
import * as acm from "aws-cdk-lib/aws-certificatemanager";
import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as origins from "aws-cdk-lib/aws-cloudfront-origins";
import * as route53 from "aws-cdk-lib/aws-route53";
import * as targets from "aws-cdk-lib/aws-route53-targets";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as s3deploy from "aws-cdk-lib/aws-s3-deployment";
import { Construct } from "constructs";
import * as path from "path";

export interface UiStackProps extends cdk.StackProps {
  projectName?: string;
  /** API Gateway base URL (no trailing slash). Adds CloudFront proxy behaviors when set. */
  apiEndpoint?: string;
  /** Custom hostname, e.g. rag.automatico.llc */
  domainName?: string;
  /** Parent hosted zone (defaults to parent of domainName). */
  hostedZoneDomain?: string;
  /** Optional fixed hosted zone id to skip Route53 lookup. */
  hostedZoneId?: string;
}

export class UiStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: UiStackProps = {}) {
    super(scope, id, props);

    const projectName = props.projectName ?? "langrag-clean";
    const repoRoot = path.join(__dirname, "..", "..");
    const paused =
      this.node.tryGetContext("paused") === true ||
      this.node.tryGetContext("paused") === "true";
    const domainName = props.domainName;
    const hostedZoneDomain =
      props.hostedZoneDomain ??
      (domainName ? domainName.split(".").slice(1).join(".") : undefined);

    const uiBucket = new s3.Bucket(this, "UiBucket", {
      bucketName: `${projectName}-ui-${this.account}-${this.region}`,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    const additionalBehaviors: Record<string, cloudfront.BehaviorOptions> = {};

    if (props.apiEndpoint && !paused) {
      const apiHost = props.apiEndpoint.replace(/^https?:\/\//, "").replace(/\/$/, "");
      const apiOrigin = new origins.HttpOrigin(apiHost, {
        protocolPolicy: cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
      });
      const apiBehavior: cloudfront.BehaviorOptions = {
        origin: apiOrigin,
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        originRequestPolicy:
          cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
      };
      for (const route of ["/ask", "/resume", "/health"]) {
        additionalBehaviors[route] = apiBehavior;
      }
      additionalBehaviors["/rag/*"] = apiBehavior;
      additionalBehaviors["/ingest/*"] = apiBehavior;
    }

    let certificate: acm.ICertificate | undefined;
    let zone: route53.IHostedZone | undefined;

    if (domainName && hostedZoneDomain) {
      zone = props.hostedZoneId
        ? route53.HostedZone.fromHostedZoneAttributes(this, "HostedZone", {
            hostedZoneId: props.hostedZoneId,
            zoneName: hostedZoneDomain,
          })
        : route53.HostedZone.fromLookup(this, "HostedZone", {
            domainName: hostedZoneDomain,
          });

      // Stack is us-east-1; CloudFront requires ACM certs there.
      certificate = new acm.Certificate(this, "SiteCertificate", {
        domainName,
        validation: acm.CertificateValidation.fromDns(zone),
      });
    }

    const distribution = new cloudfront.Distribution(this, "UiDistribution", {
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(uiBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      },
      defaultRootObject: "index.html",
      domainNames: domainName ? [domainName] : undefined,
      certificate,
      errorResponses: [
        {
          httpStatus: 403,
          responseHttpStatus: 200,
          responsePagePath: "/index.html",
          ttl: cdk.Duration.seconds(0),
        },
        {
          httpStatus: 404,
          responseHttpStatus: 200,
          responsePagePath: "/index.html",
          ttl: cdk.Duration.seconds(0),
        },
      ],
      additionalBehaviors,
    });

    if (domainName && zone) {
      const recordName = domainName.replace(new RegExp(`\\.?${hostedZoneDomain}$`), "");
      new route53.ARecord(this, "AliasA", {
        zone,
        recordName,
        target: route53.RecordTarget.fromAlias(
          new targets.CloudFrontTarget(distribution),
        ),
      });
      new route53.AaaaRecord(this, "AliasAAAA", {
        zone,
        recordName,
        target: route53.RecordTarget.fromAlias(
          new targets.CloudFrontTarget(distribution),
        ),
      });
    }

    new s3deploy.BucketDeployment(this, "DeployUi", {
      sources: [
        s3deploy.Source.asset(
          path.join(repoRoot, "ui", paused ? "paused" : "dist"),
        ),
      ],
      destinationBucket: uiBucket,
      distribution,
      distributionPaths: ["/*"],
    });

    new cdk.CfnOutput(this, "UiUrl", {
      value: domainName
        ? `https://${domainName}`
        : `https://${distribution.domainName}`,
    });
    new cdk.CfnOutput(this, "CloudFrontUrl", {
      value: `https://${distribution.domainName}`,
    });
    new cdk.CfnOutput(this, "UiBucketName", { value: uiBucket.bucketName });
    new cdk.CfnOutput(this, "DistributionId", {
      value: distribution.distributionId,
    });
  }
}
