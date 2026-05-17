import re
from datetime import datetime, timezone

from computeedge.exceptions import DeploymentError
from computeedge.models import HealthStatus, ResourceUsage
from computeedge.state.manager import StateManager
from computeedge.utils.logger import get_logger
from computeedge.utils.ssh import SSHClient

logger = get_logger("monitoring")

# Thresholds
CPU_DEGRADED = 80.0
CPU_CRITICAL = 95.0
RAM_DEGRADED = 85.0
RAM_CRITICAL = 95.0
DISK_DEGRADED = 80.0
DISK_CRITICAL = 90.0

DOCKER_STATS_CMD = "docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}'"
DF_CMD = "df -h /"
UPTIME_CMD = "uptime -s"


class MonitoringService:
    """SSH-based health checking for deployed servers."""

    def __init__(self, state: StateManager):
        self._state = state
        self._ssh = SSHClient()

    async def check_health(self, deployment_id: str, user_id: int) -> HealthStatus:
        """SSH into deployed server and check health."""
        deployment = await self._state.get(deployment_id, user_id)
        if deployment is None:
            raise DeploymentError(f"Deployment not found: {deployment_id}")

        ip = deployment["ip"]
        ssh_key = deployment["ssh_key_path"]
        monthly_cost = deployment.get("monthly_cost", 0.0)

        # Try to connect
        try:
            conn = await self._ssh.connect(ip, ssh_key)
        except Exception as e:
            logger.warning("Cannot connect to %s: %s", ip, e)
            return HealthStatus(
                status="down",
                uptime="unknown",
                monthly_cost=monthly_cost,
                assessment=f"App appears to be down. Cannot SSH into server at {ip}.",
                alerts=["Cannot connect to server — it may be offline or the IP may have changed."],
            )

        try:
            stats_output = await self._ssh.run(conn, DOCKER_STATS_CMD)
            df_output = await self._ssh.run(conn, DF_CMD)
            uptime_output = await self._ssh.run(conn, UPTIME_CMD)
        except Exception as e:
            logger.warning("Command failed on %s: %s", ip, e)
            return HealthStatus(
                status="down",
                uptime="unknown",
                monthly_cost=monthly_cost,
                assessment=f"Cannot run diagnostic commands on server. Error: {e}",
                alerts=["Failed to collect server metrics"],
            )

        # Parse outputs
        resources = self._parse_stats(stats_output, df_output)
        uptime = self._parse_uptime(uptime_output)
        alerts = self._check_thresholds(resources)

        # Check for no containers
        if stats_output.strip() == "":
            return HealthStatus(
                status="down",
                uptime=uptime,
                resources=resources,
                monthly_cost=monthly_cost,
                assessment="No containers are running. The app may have crashed.",
                alerts=["No running containers detected"],
            )

        # Determine status
        if any("Critical" in a for a in alerts):
            status = "degraded"
        elif alerts:
            status = "degraded"
        else:
            status = "healthy"

        assessment = self._build_assessment(status, resources)

        return HealthStatus(
            status=status,
            uptime=uptime,
            resources=resources,
            monthly_cost=monthly_cost,
            assessment=assessment,
            alerts=alerts,
        )

    def _parse_stats(self, stats_output: str, df_output: str) -> ResourceUsage:
        """Parse docker stats and df output into ResourceUsage."""
        total_cpu = 0.0
        total_ram_used = 0
        total_ram_total = 0

        for line in stats_output.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                # CPU: "2.50%"
                cpu_str = parts[1].strip().rstrip("%")
                try:
                    total_cpu += float(cpu_str)
                except ValueError:
                    pass

                # Memory: "120MiB / 4GiB"
                mem_match = re.match(
                    r"([\d.]+)(MiB|GiB)\s*/\s*([\d.]+)(MiB|GiB)",
                    parts[2].strip(),
                )
                if mem_match:
                    used = float(mem_match.group(1))
                    if mem_match.group(2) == "GiB":
                        used *= 1024
                    total = float(mem_match.group(3))
                    if mem_match.group(4) == "GiB":
                        total *= 1024
                    total_ram_used += int(used)
                    total_ram_total = max(total_ram_total, int(total))

        ram_percent = (total_ram_used / total_ram_total * 100) if total_ram_total > 0 else 0.0

        # Parse df output
        disk_percent = 0.0
        disk_used = 0.0
        disk_total = 0.0
        for line in df_output.strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 5:
                disk_percent = float(parts[4].rstrip("%"))
                disk_total = self._parse_size_gb(parts[1])
                disk_used = self._parse_size_gb(parts[2])
                break

        return ResourceUsage(
            cpu_usage_percent=round(total_cpu, 1),
            ram_usage_percent=round(ram_percent, 1),
            ram_used_mb=total_ram_used,
            ram_total_mb=total_ram_total,
            disk_usage_percent=disk_percent,
            disk_used_gb=round(disk_used, 1),
            disk_total_gb=round(disk_total, 1),
        )

    def _parse_size_gb(self, size_str: str) -> float:
        """Parse sizes like '40G', '12G', '500M' to GB."""
        size_str = size_str.strip()
        if size_str.endswith("G"):
            return float(size_str[:-1])
        if size_str.endswith("M"):
            return float(size_str[:-1]) / 1024
        if size_str.endswith("T"):
            return float(size_str[:-1]) * 1024
        return 0.0

    def _parse_uptime(self, uptime_output: str) -> str:
        """Parse 'uptime -s' output to human-readable duration."""
        try:
            boot_time = datetime.fromisoformat(uptime_output.strip())
            boot_time = boot_time.replace(tzinfo=timezone.utc)
            delta = datetime.now(timezone.utc) - boot_time
            days = delta.days
            hours = delta.seconds // 3600
            if days > 0:
                return f"{days} days"
            if hours > 0:
                return f"{hours} hours"
            return f"{delta.seconds // 60} minutes"
        except (ValueError, TypeError):
            return "unknown"

    def _check_thresholds(self, resources: ResourceUsage) -> list[str]:
        """Check resource usage against thresholds, return alerts."""
        alerts = []

        if resources.cpu_usage_percent >= CPU_CRITICAL:
            alerts.append(f"Critical: CPU usage at {resources.cpu_usage_percent}%")
        elif resources.cpu_usage_percent >= CPU_DEGRADED:
            alerts.append(f"CPU usage at {resources.cpu_usage_percent}%")

        if resources.ram_usage_percent >= RAM_CRITICAL:
            alerts.append(f"Critical: RAM usage at {resources.ram_usage_percent}%")
        elif resources.ram_usage_percent >= RAM_DEGRADED:
            alerts.append(f"RAM usage at {resources.ram_usage_percent}%")

        if resources.disk_usage_percent >= DISK_CRITICAL:
            alerts.append(f"Critical: Disk usage at {resources.disk_usage_percent}%")
        elif resources.disk_usage_percent >= DISK_DEGRADED:
            alerts.append(f"Disk usage at {resources.disk_usage_percent}%")

        return alerts

    def _build_assessment(self, status: str, resources: ResourceUsage) -> str:
        """Generate human-readable assessment."""
        if status == "healthy":
            return "Your app is healthy. No changes needed."
        parts = []
        if resources.cpu_usage_percent >= CPU_DEGRADED:
            parts.append(
                f"CPU usage is elevated at {resources.cpu_usage_percent}%. "
                "Monitor during peak hours — consider upgrading if this persists."
            )
        if resources.ram_usage_percent >= RAM_DEGRADED:
            parts.append(
                f"RAM usage is high at {resources.ram_usage_percent}%. "
                "Consider upgrading to a larger plan."
            )
        if resources.disk_usage_percent >= DISK_DEGRADED:
            parts.append(
                f"Disk usage is at {resources.disk_usage_percent}%. "
                "Clean up old images or upgrade storage."
            )
        return " ".join(parts) if parts else "Some metrics are elevated. Monitor closely."
