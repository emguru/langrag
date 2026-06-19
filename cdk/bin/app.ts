#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { DemoStack } from "../lib/demo-stack";

const app = new cdk.App();

const clusterName =
  app.node.tryGetContext("clusterName") ?? "kube-rag-langg";

new DemoStack(app, "KubeRagLanggDemo", {
  stackName: "KubeRagLanggDemo",
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION ?? "us-east-1",
  },
  clusterName,
  demoMode: app.node.tryGetContext("demoMode") ?? true,
  enableRds: app.node.tryGetContext("enableRds") ?? false,
  enableElastiCache: app.node.tryGetContext("enableElastiCache") ?? false,
  nodeInstanceType: app.node.tryGetContext("nodeInstanceType") ?? "t3.large",
  nodeMinSize: app.node.tryGetContext("nodeMinSize") ?? 0,
  nodeMaxSize: app.node.tryGetContext("nodeMaxSize") ?? 2,
  nodeDesiredSize: app.node.tryGetContext("nodeDesiredSize") ?? 1,
  tags: {
    Project: "kube-rag-langg",
    ManagedBy: "cdk",
    Environment: "demo",
  },
});

app.synth();
