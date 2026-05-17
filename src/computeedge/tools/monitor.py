from dataclasses import asdict

from computeedge.exceptions import ComputeEdgeError
from computeedge.services.monitoring import MonitoringService


def make_monitor_tool(monitoring_service: MonitoringService, state_manager):
    async def monitor(deployment_id: str, user_id: int) -> dict:
        """Check the health and resource usage of a deployed application."""
        try:
            if not deployment_id.startswith("ce-"):
                return {"error": f"Invalid deployment ID format. Expected 'ce-...' but got: {deployment_id}"}

            result = await monitoring_service.check_health(deployment_id, user_id)
            return asdict(result)

        except ComputeEdgeError as e:
            return {"error": str(e)}

    return monitor
