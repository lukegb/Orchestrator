from typing import List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from orchestrator.models import PortAllocation
from orchestrator.config import RepoPort


class PortAllocationError(Exception):
    pass


async def allocate_ports(
    session: AsyncSession,
    deployment_id: int,
    port_configs: List[RepoPort],
    pool_start: int,
    pool_end: int,
) -> List[PortAllocation]:
    """Allocates available ports for a deployment."""
    if not port_configs:
        return []

    # Get currently used ports
    query = select(PortAllocation.port)
    result = await session.execute(query)
    used_ports = set(result.scalars().all())

    allocated = []
    current_port = pool_start
    for port_config in port_configs:
        while current_port in used_ports and current_port <= pool_end:
            current_port += 1

        if current_port > pool_end:
            raise PortAllocationError(
                "No available ports in the configured pool range."
            )

        allocation = PortAllocation(
            deployment_id=deployment_id, port=current_port, name=port_config.name
        )
        session.add(allocation)
        allocated.append(allocation)
        used_ports.add(current_port)
        current_port += 1

    await session.commit()
    return allocated


async def release_ports(session: AsyncSession, deployment_id: int) -> None:
    """Releases ports for a deployment."""
    query = select(PortAllocation).where(PortAllocation.deployment_id == deployment_id)
    result = await session.execute(query)
    allocations = result.scalars().all()

    for allocation in allocations:
        await session.delete(allocation)

    await session.commit()
