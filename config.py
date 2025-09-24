#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置管理模块
处理环境变量、系统配置和应用设置
"""

import os
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TelegramConfig:
    """Telegram机器人配置"""
    token: str
    webhook_url: Optional[str] = None
    webhook_path: Optional[str] = None
    max_connections: int = 40
    allowed_updates: Optional[list] = None
    use_webhook: bool = False
    
    @property
    def bot_token(self) -> str:
        """兼容性属性，返回token"""
        return self.token


@dataclass
class CrawlerConfig:
    """爬虫配置"""
    target_url: str
    max_matches: int = 12
    request_timeout: int = 30
    retry_attempts: int = 3
    retry_delay: float = 1.0
    user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    headers: Dict[str, str] = None
    
    def __post_init__(self):
        if self.headers is None:
            self.headers = {
                'User-Agent': self.user_agent,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            }


@dataclass
class CacheConfig:
    """缓存配置"""
    use_redis: bool = False
    redis_url: str = "redis://localhost:6379/0"
    default_expire: int = 3600  # 默认过期时间（秒）
    max_size: int = 1000  # 最大缓存条目数
    max_memory_entries: int = 1000  # 内存缓存最大条目数


@dataclass
class DatabaseConfig:
    """数据库配置"""
    url: Optional[str] = None
    max_connections: int = 10
    timeout: int = 30


@dataclass
class LoggingConfig:
    """日志配置"""
    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file_path: Optional[str] = None
    max_file_size: int = 10 * 1024 * 1024  # 10MB
    backup_count: int = 5


@dataclass
class DeploymentConfig:
    """部署配置"""
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    workers: int = 1
    environment: str = "production"


class Config:
    """主配置类"""
    
    def __init__(self):
        self._load_env_file()
        self.telegram = self._load_telegram_config()
        self.crawler = self._load_crawler_config()
        self.cache = self._load_cache_config()
        self.database = self._load_database_config()
        self.logging = self._load_logging_config()
        self.deployment = self._load_deployment_config()
        
        # 系统配置
        self.timezone = os.getenv('TIMEZONE', 'Asia/Kuala_Lumpur')
        self.max_memory_usage = int(os.getenv('MAX_MEMORY_MB', '256'))  # MB
        self.health_check_interval = int(os.getenv('HEALTH_CHECK_INTERVAL', '60'))  # 秒
        
        # 验证必需配置
        self._validate_config()
    
    def _load_env_file(self):
        """加载.env文件"""
        env_file = Path('.env')
        if env_file.exists():
            try:
                with open(env_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, value = line.split('=', 1)
                            os.environ.setdefault(key.strip(), value.strip())
            except Exception as e:
                print(f"Warning: Failed to load .env file: {e}")
    
    def _load_telegram_config(self) -> TelegramConfig:
        """加载Telegram配置"""
        webhook_url = os.getenv('TELEGRAM_WEBHOOK_URL')
        return TelegramConfig(
            token=os.getenv('TELEGRAM_BOT_TOKEN', ''),
            webhook_url=webhook_url,
            webhook_path=os.getenv('TELEGRAM_WEBHOOK_PATH', '/webhook'),
            max_connections=int(os.getenv('TELEGRAM_MAX_CONNECTIONS', '40')),
            allowed_updates=os.getenv('TELEGRAM_ALLOWED_UPDATES', '').split(',') if os.getenv('TELEGRAM_ALLOWED_UPDATES') else None,
            use_webhook=bool(webhook_url)
        )
    
    def _load_crawler_config(self) -> CrawlerConfig:
        """加载爬虫配置"""
        return CrawlerConfig(
            target_url=os.getenv('CRAWLER_TARGET_URL', 'https://example.com/sports/soccer'),
            max_matches=int(os.getenv('CRAWLER_MAX_MATCHES', '12')),
            request_timeout=int(os.getenv('CRAWLER_TIMEOUT', '30')),
            retry_attempts=int(os.getenv('CRAWLER_RETRY_ATTEMPTS', '3')),
            retry_delay=float(os.getenv('CRAWLER_RETRY_DELAY', '1.0')),
            user_agent=os.getenv('CRAWLER_USER_AGENT', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        )
    
    def _load_cache_config(self) -> CacheConfig:
        """加载缓存配置"""
        return CacheConfig(
            use_redis=os.getenv('USE_REDIS', 'false').lower() == 'true',
            redis_url=os.getenv('REDIS_URL', 'redis://localhost:6379/0'),
            default_expire=int(os.getenv('CACHE_DEFAULT_EXPIRE', '3600')),
            max_size=int(os.getenv('CACHE_MAX_SIZE', '1000')),
            max_memory_entries=int(os.getenv('CACHE_MAX_MEMORY_ENTRIES', '1000'))
        )
    
    def _load_database_config(self) -> DatabaseConfig:
        """加载数据库配置"""
        return DatabaseConfig(
            url=os.getenv('DATABASE_URL'),
            max_connections=int(os.getenv('DB_MAX_CONNECTIONS', '10')),
            timeout=int(os.getenv('DB_TIMEOUT', '30'))
        )
    
    def _load_logging_config(self) -> LoggingConfig:
        """加载日志配置"""
        return LoggingConfig(
            level=os.getenv('LOG_LEVEL', 'INFO').upper(),
            format=os.getenv('LOG_FORMAT', '%(asctime)s - %(name)s - %(levelname)s - %(message)s'),
            file_path=os.getenv('LOG_FILE_PATH'),
            max_file_size=int(os.getenv('LOG_MAX_FILE_SIZE', str(10 * 1024 * 1024))),
            backup_count=int(os.getenv('LOG_BACKUP_COUNT', '5'))
        )
    
    def _load_deployment_config(self) -> DeploymentConfig:
        """加载部署配置"""
        return DeploymentConfig(
            host=os.getenv('HOST', '0.0.0.0'),
            port=int(os.getenv('PORT', '8000')),
            debug=os.getenv('DEBUG', 'false').lower() == 'true',
            workers=int(os.getenv('WORKERS', '1')),
            environment=os.getenv('ENVIRONMENT', 'production')
        )
    
    def _validate_config(self):
        """验证配置"""
        errors = []
        
        # 检查是否为测试模式或健康检查模式
        import sys
        is_test_mode = (
            len(sys.argv) > 1 and (sys.argv[1].startswith('test') or sys.argv[1] == 'health') or
            os.getenv('TESTING_MODE', '').lower() == 'true' or
            'test' in sys.argv[0].lower()  # 检查脚本名称是否包含test
        )
        
        # 验证必需的配置（测试模式下跳过Telegram验证）
        if not is_test_mode and not self.telegram.token:
            errors.append("TELEGRAM_BOT_TOKEN is required")
        
        if not is_test_mode and self.telegram.use_webhook and not self.telegram.webhook_url:
            errors.append("TELEGRAM_WEBHOOK_URL is required when using webhook")
        
        if not self.crawler.target_url:
            errors.append("CRAWLER_TARGET_URL is required")
        
        if errors:
            raise ValueError(f"Configuration errors: {', '.join(errors)}")
    
    def get_log_level(self) -> int:
        """获取日志级别"""
        level_map = {
            'DEBUG': logging.DEBUG,
            'INFO': logging.INFO,
            'WARNING': logging.WARNING,
            'ERROR': logging.ERROR,
            'CRITICAL': logging.CRITICAL
        }
        return level_map.get(self.logging.level, logging.INFO)
    
    def is_development(self) -> bool:
        """检查是否为开发环境"""
        return self.deployment.environment.lower() in ['development', 'dev', 'local']
    
    def is_production(self) -> bool:
        """检查是否为生产环境"""
        return self.deployment.environment.lower() in ['production', 'prod']
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式（用于调试）"""
        return {
            'telegram': {
                'token': '***' if self.telegram.token else None,
                'webhook_url': self.telegram.webhook_url,
                'max_connections': self.telegram.max_connections
            },
            'crawler': {
                'target_url': self.crawler.target_url,
                'max_matches': self.crawler.max_matches,
                'request_timeout': self.crawler.request_timeout,
                'retry_attempts': self.crawler.retry_attempts
            },
            'cache': {
                'ttl': self.cache.ttl,
                'max_size': self.cache.max_size,
                'use_redis': self.cache.use_redis
            },
            'deployment': {
                'host': self.deployment.host,
                'port': self.deployment.port,
                'environment': self.deployment.environment,
                'debug': self.deployment.debug
            },
            'timezone': self.timezone,
            'max_memory_usage': self.max_memory_usage
        }


# 全局配置实例（延迟初始化）
_config = None


def get_config() -> Config:
    """获取配置实例（延迟初始化）"""
    global _config
    if _config is None:
        _config = Config()
    return _config


def reload_config() -> Config:
    """重新加载配置"""
    global _config
    _config = Config()
    return _config
