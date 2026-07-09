"""Agendador APScheduler."""
import os
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from .generator import gerar_edicao_sync

log = logging.getLogger(__name__)


def iniciar_scheduler() -> BackgroundScheduler:
    horario = os.getenv("DAILY_TIME", "12:00")
    tz = os.getenv("TIMEZONE", "America/Sao_Paulo")
    hora, minuto = horario.split(":")
    scheduler = BackgroundScheduler(timezone=tz)
    scheduler.add_job(
        _job,
        CronTrigger(hour=int(hora), minute=int(minuto)),
        id="newsletter_diaria",
        replace_existing=True,
    )
    scheduler.start()
    log.info("Scheduler iniciado: %s (%s)", horario, tz)
    return scheduler


def _job():
    try:
        log.info("Disparando geração diária da newsletter...")
        edicao_id = gerar_edicao_sync()
        log.info("Geração concluída: edição #%s", edicao_id)
    except Exception:
        log.exception("Erro na geração diária")
