"""CLI entrypoint for PolyEdge."""

import asyncio
import click
import logging


@click.group()
def cli():
    """PolyEdge - Polymarket prediction engine."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")


@cli.command()
def serve():
    """Run the FastAPI server."""
    import uvicorn
    uvicorn.run("polyedge.app:app", host="0.0.0.0", port=8090, reload=False)


@cli.command()
def run():
    """Run the scheduler (poller + research + predictions)."""
    from polyedge.scheduler import run_forever
    asyncio.run(run_forever())


@cli.command()
def poll():
    """Run a single poll cycle."""
    from polyedge.poller import PolymarketPoller

    async def _poll():
        p = PolymarketPoller()
        count = await p.poll_all()
        click.echo(f"Polled {count} markets")
        await p.close()

    asyncio.run(_poll())


@cli.command()
def stats():
    """Show current stats."""
    from polyedge.db import SessionLocal
    from polyedge.models import Market, Factor, Prediction
    from sqlalchemy import select, func

    async def _stats():
        async with SessionLocal() as session:
            markets = (await session.execute(select(func.count(Market.id)))).scalar()
            active = (await session.execute(select(func.count(Market.id)).where(Market.active == True))).scalar()
            factors = (await session.execute(select(func.count(Factor.id)))).scalar()
            preds = (await session.execute(select(func.count(Prediction.id)))).scalar()
            click.echo(f"Markets: {markets} ({active} active)")
            click.echo(f"Factors: {factors}")
            click.echo(f"Predictions: {preds}")

    asyncio.run(_stats())


if __name__ == "__main__":
    cli()
