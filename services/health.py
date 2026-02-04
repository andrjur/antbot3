"""
Health-check сервис для мониторинга состояния бота
"""
import asyncio
import aiosqlite
import logging
from datetime import datetime
from typing import Dict, List, Optional
import aiohttp

logger = logging.getLogger(__name__)


class HealthChecker:
    """Сервис проверки здоровья бота"""
    
    def __init__(self, db_file: str, bot_token: str, admin_group_id: int, 
                 n8n_domain: str, webhook_url: str):
        self.db_file = db_file
        self.bot_token = bot_token
        self.admin_group_id = admin_group_id
        self.n8n_domain = n8n_domain
        self.webhook_url = webhook_url
        self.start_time = datetime.now()
        self.checks: Dict[str, Dict] = {}
        
    async def check_database(self) -> Dict:
        """Проверка подключения к базе данных"""
        try:
            async with aiosqlite.connect(self.db_file) as conn:
                cursor = await conn.execute("SELECT 1")
                await cursor.fetchone()
                
                # Проверяем количество таблиц
                cursor = await conn.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type='table'"
                )
                table_count = await cursor.fetchone()
                
            return {
                'status': 'healthy',
                'tables_count': table_count[0],
                'response_time_ms': 0  # Можно добавить замер времени
            }
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return {
                'status': 'unhealthy',
                'error': str(e)
            }
    
    async def check_telegram_api(self) -> Dict:
        """Проверка доступности Telegram API"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"https://api.telegram.org/bot{self.bot_token}/getMe",
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get('ok'):
                            return {
                                'status': 'healthy',
                                'bot_name': data['result'].get('username', 'unknown'),
                                'response_time_ms': 0
                            }
                    return {
                        'status': 'unhealthy',
                        'error': f'HTTP {response.status}'
                    }
        except asyncio.TimeoutError:
            return {
                'status': 'unhealthy',
                'error': 'Timeout'
            }
        except Exception as e:
            logger.error(f"Telegram API health check failed: {e}")
            return {
                'status': 'unhealthy',
                'error': str(e)
            }
    
    async def check_n8n_webhook(self) -> Dict:
        """Проверка доступности n8n вебхуков"""
        try:
            async with aiohttp.ClientSession() as session:
                # Проверяем базовый доступ к n8n домену
                async with session.get(
                    self.n8n_domain,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    return {
                        'status': 'healthy' if response.status < 500 else 'degraded',
                        'http_status': response.status,
                        'response_time_ms': 0
                    }
        except asyncio.TimeoutError:
            return {
                'status': 'unhealthy',
                'error': 'Timeout'
            }
        except Exception as e:
            logger.error(f"N8N webhook health check failed: {e}")
            return {
                'status': 'unhealthy',
                'error': str(e)
            }
    
    async def check_webhook_endpoint(self) -> Dict:
        """Проверка доступности webhook endpoint"""
        try:
            # Проверяем только базовый доступ (OPTIONS запрос)
            async with aiohttp.ClientSession() as session:
                async with session.options(
                    self.webhook_url,
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as response:
                    return {
                        'status': 'healthy',
                        'http_status': response.status,
                        'response_time_ms': 0
                    }
        except Exception as e:
            # OPTIONS может не поддерживаться, это нормально
            return {
                'status': 'healthy',
                'note': 'Webhook endpoint configured'
            }
    
    async def check_disk_space(self) -> Dict:
        """Проверка свободного места на диске"""
        try:
            import shutil
            stat = shutil.disk_usage(".")
            free_gb = stat.free / (1024**3)
            total_gb = stat.total / (1024**3)
            usage_percent = (stat.used / stat.total) * 100
            
            status = 'healthy'
            if usage_percent > 90:
                status = 'critical'
            elif usage_percent > 80:
                status = 'warning'
            
            return {
                'status': status,
                'free_gb': round(free_gb, 2),
                'total_gb': round(total_gb, 2),
                'usage_percent': round(usage_percent, 2)
            }
        except Exception as e:
            logger.error(f"Disk space check failed: {e}")
            return {
                'status': 'unknown',
                'error': str(e)
            }
    
    async def get_uptime(self) -> Dict:
        """Получение времени работы бота"""
        uptime = datetime.now() - self.start_time
        total_seconds = int(uptime.total_seconds())
        
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60
        
        return {
            'status': 'healthy',
            'total_seconds': total_seconds,
            'formatted': f"{days}d {hours}h {minutes}m"
        }
    
    async def run_all_checks(self) -> Dict:
        """Запуск всех проверок здоровья"""
        checks = {
            'database': await self.check_database(),
            'telegram_api': await self.check_telegram_api(),
            'n8n_webhook': await self.check_n8n_webhook(),
            'webhook_endpoint': await self.check_webhook_endpoint(),
            'disk_space': await self.check_disk_space(),
            'uptime': await self.get_uptime()
        }
        
        # Определяем общий статус
        overall_status = 'healthy'
        for check_name, check_result in checks.items():
            if check_result.get('status') == 'critical':
                overall_status = 'critical'
                break
            elif check_result.get('status') == 'unhealthy' and overall_status != 'critical':
                overall_status = 'unhealthy'
            elif check_result.get('status') == 'warning' and overall_status == 'healthy':
                overall_status = 'warning'
        
        self.checks = checks
        
        return {
            'status': overall_status,
            'timestamp': datetime.now().isoformat(),
            'checks': checks
        }
    
    def is_healthy(self) -> bool:
        """Проверка, что все компоненты здоровы"""
        if not self.checks:
            return False
        
        for check_result in self.checks.values():
            if check_result.get('status') in ['unhealthy', 'critical']:
                return False
        
        return True


async def health_check_endpoint(request):
    """HTTP endpoint для health-check"""
    from aiohttp import web
    
    checker = request.app.get('health_checker')
    if not checker:
        return web.json_response(
            {'status': 'error', 'message': 'Health checker not initialized'},
            status=500
        )
    
    result = await checker.run_all_checks()
    
    # Определяем HTTP статус
    if result['status'] == 'healthy':
        status_code = 200
    elif result['status'] == 'warning':
        status_code = 200  # Всё ещё работает, но с предупреждениями
    else:
        status_code = 503  # Service Unavailable
    
    return web.json_response(result, status=status_code)


async def liveness_probe(request):
    """Проверка, что бот жив (kubernetes liveness probe)"""
    from aiohttp import web
    return web.json_response({'status': 'alive'})


async def readiness_probe(request):
    """Проверка, что бот готов принимать запросы (kubernetes readiness probe)"""
    from aiohttp import web
    
    checker = request.app.get('health_checker')
    if not checker:
        return web.json_response(
            {'status': 'not_ready'},
            status=503
        )
    
    # Проверяем только критические компоненты
    db_check = await checker.check_database()
    tg_check = await checker.check_telegram_api()
    
    if db_check['status'] == 'healthy' and tg_check['status'] == 'healthy':
        return web.json_response({'status': 'ready'})
    else:
        return web.json_response(
            {
                'status': 'not_ready',
                'database': db_check['status'],
                'telegram_api': tg_check['status']
            },
            status=503
        )
