#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { CleanRagStack } from "../lib/clean-rag-stack";
import { DemoStack } from "../lib/demo-stack";
import { UiStack } from "../lib/ui-stack";

const app = new cdk.App();
const useCleanRag = app.node.tryGetContext("cleanRag") ?? true;
const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION ?? "us-east-1",
};

new UiStack(app, "LangragUi", {
  stackName: "LangragUi",
  env,
  projectName: "langrag-clean",
  apiEndpoint: app.node.tryGetContext("apiEndpoint") as string | undefined,
  domainName:
    (app.node.tryGetContext("domainName") as string | undefined) ??
    "rag.automatico.llc",
  hostedZoneDomain:
    (app.node.tryGetContext("hostedZoneDomain") as string | undefined) ??
    "automatico.llc",
  hostedZoneId:
    (app.node.tryGetContext("hostedZoneId") as string | undefined) ??
    "Z03675412E5MU377HS9OE",
});

if (useCleanRag) {
  new CleanRagStack(app, "LangragCleanRag", {
    stackName: "LangragCleanRag",
    env: {
      account: process.env.CDK_DEFAULT_ACCOUNT,
      region: process.env.CDK_DEFAULT_REGION ?? "us-east-1",
    },
    projectName: "langrag-clean",
  });
} else {
  const clusterName =
    app.node.tryGetContext("clusterName") ?? "kube-rag-langg";
  const environment =
    (app.node.tryGetContext("environment") as string | undefined) ??
    ((app.node.tryGetContext("demoMode") ?? true) ? "demo" : "production");
  new DemoStack(app, "KubeRagLanggDemo", {
    stackName: "KubeRagLanggDemo",
    env: {
      account: process.env.CDK_DEFAULT_ACCOUNT,
      region: process.env.CDK_DEFAULT_REGION ?? "us-east-1",
    },
    clusterName,
    environment,
    demoMode: app.node.tryGetContext("demoMode") ?? true,
    enableRds: app.node.tryGetContext("enableRds") ?? false,
    enableElastiCache: app.node.tryGetContext("enableElastiCache") ?? false,
    nodeInstanceType: app.node.tryGetContext("nodeInstanceType") ?? "t3.large",
    nodeMinSize: app.node.tryGetContext("nodeMinSize") ?? 0,
    nodeMaxSize: app.node.tryGetContext("nodeMaxSize") ?? 2,
    nodeDesiredSize: app.node.tryGetContext("nodeDesiredSize") ?? 1,
  });
}

app.synth();
