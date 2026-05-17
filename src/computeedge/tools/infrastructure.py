from dataclasses import asdict

from computeedge.exceptions import ComputeEdgeError
from computeedge.services.infrastructure import InfrastructureLifecycleService


def make_show_infra_tool(infrastructure_service: InfrastructureLifecycleService):
    async def show_infra(deployment_id: str, user_id: int) -> dict:
        try:
            if not deployment_id.startswith("ce-"):
                return {"error": f"Invalid deployment ID format. Expected 'ce-...' but got: {deployment_id}"}
            result = await infrastructure_service.show(deployment_id, user_id)
            return asdict(result)
        except ComputeEdgeError as e:
            return {"error": str(e)}

    return show_infra


def make_refresh_infra_tool(infrastructure_service: InfrastructureLifecycleService):
    async def refresh_infra(deployment_id: str, user_id: int) -> dict:
        try:
            if not deployment_id.startswith("ce-"):
                return {"error": f"Invalid deployment ID format. Expected 'ce-...' but got: {deployment_id}"}
            result = await infrastructure_service.refresh(deployment_id, user_id)
            return asdict(result)
        except ComputeEdgeError as e:
            return {"error": str(e)}

    return refresh_infra


def make_plan_infra_tool(infrastructure_service: InfrastructureLifecycleService):
    async def plan_infra(deployment_id: str, user_id: int) -> dict:
        try:
            if not deployment_id.startswith("ce-"):
                return {"error": f"Invalid deployment ID format. Expected 'ce-...' but got: {deployment_id}"}
            result = await infrastructure_service.plan(deployment_id, user_id)
            return asdict(result)
        except ComputeEdgeError as e:
            return {"error": str(e)}

    return plan_infra


def make_destroy_deployment_tool(infrastructure_service: InfrastructureLifecycleService):
    async def destroy_deployment(deployment_id: str, provider_token: str, user_id: int) -> dict:
        try:
            if not deployment_id.startswith("ce-"):
                return {"error": f"Invalid deployment ID format. Expected 'ce-...' but got: {deployment_id}"}
            result = await infrastructure_service.destroy(deployment_id, user_id)
            return asdict(result)
        except ComputeEdgeError as e:
            return {"error": str(e)}

    return destroy_deployment
