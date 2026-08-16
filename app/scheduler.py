"""Agendador APScheduler — múltiplos horários vindos do banco."""
import os
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from .generator import gerar_edicao_sync
from . import db

log = logging.getLogger(__name__)

TZ = os.getenv("TIMEZONE", "America/Sao_Paulo")


def _job():
    try:
        log.info("Disparando geração automática da newsletter...")
        edicao_id = gerar_edicao_sync()
        log.info("Geração concluída: edição #%s", edicao_id)
    except Exception:
        log.exception("Erro na geração automática")


def _semear_padrao():
    """Se a tabela de agendamentos estiver vazia, usa o DAILY_TIME do .env."""
    horario_env = os.getenv("DAILY_TIME", "12:00")
    try:
        h, m = horario_env.split(":")
        db.seed_agendamento_padrao(int(h), int(m))
    except Exception:
        log.exception("DAILY_TIME inválido no .env; ignorando seed.")


def _aplicar_jobs(scheduler: BackgroundScheduler):
    """Remove todos os jobs de newsletter e recria a partir do banco."""
    for job in list(scheduler.get_jobs()):
        if job.id.startswith("newsletter_"):
            scheduler.remove_job(job.id)
    ativos = db.agendamentos_ativos()
    for ag in ativos:
        scheduler.add_job(
            _job,
            CronTrigger(hour=ag["hora"], minute=ag["minuto"], timezone=TZ),
            id=f"newsletter_{ag['id']}",
            replace_existing=True,
        )
    horarios = ", ".join(f"{a['hora']:02d}:{a['minuto']:02d}" for a in ativos) or "(nenhum)"
    log.info("Agendamentos aplicados (%s): %s", TZ, horarios)


def iniciar_scheduler() -> BackgroundScheduler:
    _semear_padrao()
    scheduler = BackgroundScheduler(timezone=TZ)
    scheduler.start()
    _aplicar_jobs(scheduler)
    return scheduler


def reagendar(scheduler: BackgroundScheduler):
    """Chamado pelas rotas depois de qualquer mudança em /agenda."""
    _aplicar_jobs(scheduler)


def proximas_execucoes(scheduler: BackgroundScheduler, limit: int = 5) -> list[str]:
    """Lista formatada 'HH:MM DD/MM' das próximas N execuções, na ordem cronológica."""
    jobs = [j for j in scheduler.get_jobs() if j.id.startswith("newsletter_")]
    proximas = []
    for j in jobs:
        if j.next_run_time:
            proximas.append(j.next_run_time)
    proximas.sort()
    return [t.strftime("%H:%M · %d/%m") for t in proximas[:limit]]
