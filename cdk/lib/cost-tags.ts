import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";

/** Standard tags for AWS Cost Explorer / Cost Allocation Tags. */
export interface ProjectCostTags {
  project: string;
  environment: string;
  workload?: string;
  costCenter?: string;
  owner?: string;
}

const COST_ALLOCATION_TAG_KEYS = [
  "Project",
  "Environment",
  "Workload",
  "CostCenter",
  "Component",
  "Owner",
  "ManagedBy",
  "Application",
  "CostCategory",
] as const;

export { COST_ALLOCATION_TAG_KEYS };

export function applyProjectCostTags(
  scope: Construct,
  props: ProjectCostTags,
): void {
  const tags = cdk.Tags.of(scope);
  tags.add("Project", props.project);
  tags.add("Environment", props.environment);
  tags.add("Workload", props.workload ?? "research-showcase");
  tags.add("Application", props.project);
  tags.add("CostCategory", props.environment);
  tags.add("ManagedBy", "cdk");
  if (props.costCenter) {
    tags.add("CostCenter", props.costCenter);
  }
  if (props.owner) {
    tags.add("Owner", props.owner);
  }
}

/** Per-resource component for Cost Category rules (compute, storage, auth, …). */
export function tagComponent(scope: Construct, component: string): void {
  cdk.Tags.of(scope).add("Component", component);
}
