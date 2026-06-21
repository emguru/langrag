#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { DemoStack } from "../lib/demo-stack";

const app = new cdk.App();

const clusterName =
  app.node.tryGetContext("clusterName") ?? "kube-rag-langg";
const environment =
  (app.node.tryGetContext("environment") as string | undefined) ??
  ((app.node.tryGetContext("demoMode") ?? true) ? "demo" : "production");
const costCenter =
  (app.node.tryGetContext("costCenter") as string | undefined) ?? "research";
const costOwner =
  (app.node.tryGetContext("costOwner") as string | undefined) ?? "";

new DemoStack(app, "KubeRagLanggDemo", {
  stackName: "KubeRagLanggDemo",
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION ?? "us-east-1",
  },
  clusterName,
  environment,
  costCenter,
  costOwner,
  demoMode: app.node.tryGetContext("demoMode") ?? true,
  enableRds: app.node.tryGetContext("enableRds") ?? false,
  enableElastiCache: app.node.tryGetContext("enableElastiCache") ?? false,
  nodeInstanceType: app.node.tryGetContext("nodeInstanceType") ?? "t3.large",
  nodeMinSize: app.node.tryGetContext("nodeMinSize") ?? 0,
  nodeMaxSize: app.node.tryGetContext("nodeMaxSize") ?? 2,
  nodeDesiredSize: app.node.tryGetContext("nodeDesiredSize") ?? 1,
  tags: {
    Project: clusterName,
    Environment: environment,
    Workload: "research-showcase",
    Application: clusterName,
    CostCategory: environment,
    CostCenter: costCenter,
    ManagedBy: "cdk",
    ...(costOwner ? { Owner: costOwner } : {}),
  },
});

app.synth();
