"""
Модуль для периодических задач обновления метрик
"""
import asyncio
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class MetricsUpdater:
    """Класс для периодического обновления метрик"""
    
    def __init__(self, db_file: str, update_interval: int = 60):
        self.db_file = db_file
        self.update_interval = update_interval
        self._task = None
        self._running = False
        self._uptime_start = datetime.now()
    
    async def start(self):
        """Запускает периодическое обновление метрик"""
        if self._running:
            return
        
        self._running = True
        self._task = asyncio.create_task(self._update_loop())
        logger.info("Metrics updater started")
    
    async def stop(self):
        """Останавливает обновление метрик"""
        if not self._running:
            return
        
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Metrics updater stopped")
    
    async def _update_loop(self):
        """Цикл обновления метрик"""
        from services.metrics import (
            update_active_users_metric,
            update_active_courses_metric,
            update_pending_homework_metric,
            BOT_UPTIME
        )
        
        while self._running:
            try:
                # Обновляем метрики
                await update_active_users_metric(self.db_file)
                await update_active_courses_metric(self.db_file)
                await update_pending_homework_metric(self.db_file)
                
                # Обновляем uptime
                uptime = (datetime.now() - self._uptime_start).total_seconds()
                BOT_UPTIME.set(uptime)
                
                logger.debug("Metrics updated successfully")
            except Exception as e:
                logger.error(f"Error updating metrics: {e}")
            
            try:
                await asyncio.sleep(self.update_interval)
            except asyncio.CancelledError:
                break


async def setup_metrics_endpoints(app, db_file: str, bot_token: str, 
                                   admin_group_id: int, n8n_domain: str, 
                                   webhook_url: str):
    """Настройка эндпоинтов метрик и health-check"""
    from aiohttp import web
    from services.metrics import get_metrics_response, init_bot_info
    from services.health import (
        health_check_endpoint, 
        liveness_probe, 
        readiness_probe,
        HealthChecker
    )
    
    # Инициализация метрик
    init_bot_info(version="1.0.0", environment="production")
    
    # Создаем health checker
    health_checker = HealthChecker(
        db_file=db_file,
        bot_token=bot_token,
        admin_group_id=admin_group_id,
        n8n_domain=n8n_domain,
        webhook_url=webhook_url
    )
    app['health_checker'] = health_checker
    
    # Добавляем роуты
    app.router.add_get('/metrics', lambda r: get_metrics_response())
    app.router.add_get('/health', health_check_endpoint)
    app.router.add_get('/health/live', liveness_probe)
    app.router.add_get('/health/ready', readiness_probe)
    
    # Запускаем периодическое обновление метрик
    metrics_updater = MetricsUpdater(db_file)
    await metrics_updater.start()
    app['metrics_updater'] = metrics_updater
    
    logger.info("Metrics and health endpoints configured")
    return app
