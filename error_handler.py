#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
错误处理和重试机制模块
提供统一的异常处理、重试逻辑和错误恢复功能
"""

import asyncio
import logging
import traceback
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Type, Union
from functools import wraps
from enum import Enum
import random

from config import get_config

logger = logging.getLogger(__name__)

class ErrorType(Enum):
    """错误类型枚举"""
    NETWORK_ERROR = "network_error"
    SCRAPING_ERROR = "scraping_error"
    TELEGRAM_ERROR = "telegram_error"
    CACHE_ERROR = "cache_error"
    DATABASE_ERROR = "database_error"
    VALIDATION_ERROR = "validation_error"
    RATE_LIMIT_ERROR = "rate_limit_error"
    TIMEOUT_ERROR = "timeout_error"
    UNKNOWN_ERROR = "unknown_error"

class ErrorSeverity(Enum):
    """错误严重程度"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class ErrorInfo:
    """错误信息类"""
    
    def __init__(
        self,
        error_type: ErrorType,
        severity: ErrorSeverity,
        message: str,
        exception: Optional[Exception] = None,
        context: Optional[Dict[str, Any]] = None,
        timestamp: Optional[datetime] = None
    ):
        self.error_type = error_type
        self.severity = severity
        self.message = message
        self.exception = exception
        self.context = context or {}
        self.timestamp = timestamp or datetime.now()
        self.traceback = traceback.format_exc() if exception else None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            'error_type': self.error_type.value,
            'severity': self.severity.value,
            'message': self.message,
            'exception': str(self.exception) if self.exception else None,
            'context': self.context,
            'timestamp': self.timestamp.isoformat(),
            'traceback': self.traceback
        }
    
    def __str__(self) -> str:
        return f"[{self.severity.value.upper()}] {self.error_type.value}: {self.message}"

class RetryConfig:
    """重试配置类"""
    
    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
        backoff_strategy: str = "exponential"
    ):
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter
        self.backoff_strategy = backoff_strategy
    
    def calculate_delay(self, attempt: int) -> float:
        """计算重试延迟时间"""
        if self.backoff_strategy == "exponential":
            delay = self.base_delay * (self.exponential_base ** (attempt - 1))
        elif self.backoff_strategy == "linear":
            delay = self.base_delay * attempt
        elif self.backoff_strategy == "fixed":
            delay = self.base_delay
        else:
            delay = self.base_delay
        
        # 应用最大延迟限制
        delay = min(delay, self.max_delay)
        
        # 添加随机抖动
        if self.jitter:
            delay = delay * (0.5 + random.random() * 0.5)
        
        return delay

class ErrorHandler:
    """错误处理器类"""
    
    def __init__(self):
        self.config = get_config()
        self.error_history: List[ErrorInfo] = []
        self.max_history_size = 1000
        self.error_counts: Dict[str, int] = {}
        self.last_error_time: Dict[str, datetime] = {}
        
        # 默认重试配置
        self.default_retry_config = RetryConfig(
            max_attempts=3,
            base_delay=1.0,
            max_delay=30.0,
            exponential_base=2.0,
            jitter=True
        )
        
        # 特定错误类型的重试配置
        self.retry_configs = {
            ErrorType.NETWORK_ERROR: RetryConfig(max_attempts=5, base_delay=2.0),
            ErrorType.RATE_LIMIT_ERROR: RetryConfig(max_attempts=3, base_delay=10.0, max_delay=300.0),
            ErrorType.TIMEOUT_ERROR: RetryConfig(max_attempts=3, base_delay=5.0),
            ErrorType.SCRAPING_ERROR: RetryConfig(max_attempts=2, base_delay=3.0),
            ErrorType.TELEGRAM_ERROR: RetryConfig(max_attempts=3, base_delay=1.0),
            ErrorType.CACHE_ERROR: RetryConfig(max_attempts=2, base_delay=0.5),
        }
    
    def log_error(
        self,
        error_type: ErrorType,
        severity: ErrorSeverity,
        message: str,
        exception: Optional[Exception] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> ErrorInfo:
        """记录错误信息"""
        error_info = ErrorInfo(
            error_type=error_type,
            severity=severity,
            message=message,
            exception=exception,
            context=context
        )
        
        # 添加到历史记录
        self.error_history.append(error_info)
        
        # 限制历史记录大小
        if len(self.error_history) > self.max_history_size:
            self.error_history = self.error_history[-self.max_history_size:]
        
        # 更新错误计数
        error_key = f"{error_type.value}_{severity.value}"
        self.error_counts[error_key] = self.error_counts.get(error_key, 0) + 1
        self.last_error_time[error_key] = datetime.now()
        
        # 根据严重程度选择日志级别
        if severity == ErrorSeverity.CRITICAL:
            logger.critical(str(error_info))
        elif severity == ErrorSeverity.HIGH:
            logger.error(str(error_info))
        elif severity == ErrorSeverity.MEDIUM:
            logger.warning(str(error_info))
        else:
            logger.info(str(error_info))
        
        # 如果有异常，记录详细信息
        if exception and severity in [ErrorSeverity.HIGH, ErrorSeverity.CRITICAL]:
            logger.error(f"异常详情: {traceback.format_exc()}")
        
        return error_info
    
    def classify_exception(self, exception: Exception) -> ErrorType:
        """分类异常类型"""
        exception_name = type(exception).__name__
        exception_message = str(exception).lower()
        
        # 网络相关错误
        if any(keyword in exception_name.lower() for keyword in 
               ['connection', 'timeout', 'network', 'socket', 'http']):
            return ErrorType.NETWORK_ERROR
        
        # 超时错误
        if 'timeout' in exception_message or 'timeout' in exception_name.lower():
            return ErrorType.TIMEOUT_ERROR
        
        # 限流错误
        if any(keyword in exception_message for keyword in 
               ['rate limit', 'too many requests', '429']):
            return ErrorType.RATE_LIMIT_ERROR
        
        # Telegram相关错误
        if 'telegram' in exception_name.lower() or 'bot' in exception_name.lower():
            return ErrorType.TELEGRAM_ERROR
        
        # 爬虫相关错误
        if any(keyword in exception_name.lower() for keyword in 
               ['selenium', 'webdriver', 'element', 'parse']):
            return ErrorType.SCRAPING_ERROR
        
        # 缓存相关错误
        if any(keyword in exception_name.lower() for keyword in 
               ['redis', 'cache', 'memory']):
            return ErrorType.CACHE_ERROR
        
        # 数据库相关错误
        if any(keyword in exception_name.lower() for keyword in 
               ['database', 'sql', 'db']):
            return ErrorType.DATABASE_ERROR
        
        # 验证错误
        if any(keyword in exception_name.lower() for keyword in 
               ['validation', 'value', 'type', 'attribute']):
            return ErrorType.VALIDATION_ERROR
        
        return ErrorType.UNKNOWN_ERROR
    
    def determine_severity(self, error_type: ErrorType, exception: Exception) -> ErrorSeverity:
        """确定错误严重程度"""
        # 关键错误
        if error_type in [ErrorType.DATABASE_ERROR]:
            return ErrorSeverity.CRITICAL
        
        # 高严重性错误
        if error_type in [ErrorType.TELEGRAM_ERROR, ErrorType.SCRAPING_ERROR]:
            return ErrorSeverity.HIGH
        
        # 中等严重性错误
        if error_type in [ErrorType.NETWORK_ERROR, ErrorType.TIMEOUT_ERROR, ErrorType.RATE_LIMIT_ERROR]:
            return ErrorSeverity.MEDIUM
        
        # 低严重性错误
        return ErrorSeverity.LOW
    
    async def handle_exception(
        self,
        exception: Exception,
        context: Optional[Dict[str, Any]] = None,
        custom_message: Optional[str] = None
    ) -> ErrorInfo:
        """处理异常"""
        error_type = self.classify_exception(exception)
        severity = self.determine_severity(error_type, exception)
        message = custom_message or f"发生{error_type.value}错误: {str(exception)}"
        
        return self.log_error(
            error_type=error_type,
            severity=severity,
            message=message,
            exception=exception,
            context=context
        )
    
    def get_retry_config(self, error_type: ErrorType) -> RetryConfig:
        """获取重试配置"""
        return self.retry_configs.get(error_type, self.default_retry_config)
    
    def should_retry(self, error_type: ErrorType, attempt: int) -> bool:
        """判断是否应该重试"""
        retry_config = self.get_retry_config(error_type)
        return attempt < retry_config.max_attempts
    
    async def execute_with_retry(
        self,
        func: Callable,
        *args,
        retry_config: Optional[RetryConfig] = None,
        context: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> Any:
        """执行函数并在失败时重试"""
        last_exception = None
        
        for attempt in range(1, (retry_config or self.default_retry_config).max_attempts + 1):
            try:
                if asyncio.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                else:
                    return func(*args, **kwargs)
            
            except Exception as e:
                last_exception = e
                error_type = self.classify_exception(e)
                
                # 记录错误
                error_info = await self.handle_exception(
                    e,
                    context={**(context or {}), 'attempt': attempt, 'function': func.__name__}
                )
                
                # 检查是否应该重试
                current_retry_config = retry_config or self.get_retry_config(error_type)
                if attempt >= current_retry_config.max_attempts:
                    logger.error(f"函数 {func.__name__} 在 {attempt} 次尝试后仍然失败")
                    break
                
                # 计算延迟时间并等待
                delay = current_retry_config.calculate_delay(attempt)
                logger.info(f"函数 {func.__name__} 第 {attempt} 次尝试失败，{delay:.2f}秒后重试")
                await asyncio.sleep(delay)
        
        # 所有重试都失败了，抛出最后一个异常
        if last_exception:
            raise last_exception
    
    def get_error_stats(self) -> Dict[str, Any]:
        """获取错误统计信息"""
        now = datetime.now()
        recent_errors = [
            error for error in self.error_history
            if now - error.timestamp < timedelta(hours=24)
        ]
        
        # 按错误类型统计
        error_type_counts = {}
        for error in recent_errors:
            error_type = error.error_type.value
            error_type_counts[error_type] = error_type_counts.get(error_type, 0) + 1
        
        # 按严重程度统计
        severity_counts = {}
        for error in recent_errors:
            severity = error.severity.value
            severity_counts[severity] = severity_counts.get(severity, 0) + 1
        
        return {
            'total_errors': len(self.error_history),
            'recent_errors_24h': len(recent_errors),
            'error_type_counts': error_type_counts,
            'severity_counts': severity_counts,
            'most_common_errors': self._get_most_common_errors(),
            'error_rate_per_hour': len(recent_errors) / 24 if recent_errors else 0
        }
    
    async def get_stats(self) -> Dict[str, Any]:
        """获取统计信息（get_error_stats的异步别名）"""
        return self.get_error_stats()
    
    def _get_most_common_errors(self, limit: int = 5) -> List[Dict[str, Any]]:
        """获取最常见的错误"""
        error_counts = {}
        for error in self.error_history[-100:]:  # 只看最近100个错误
            key = f"{error.error_type.value}: {error.message[:50]}"
            if key not in error_counts:
                error_counts[key] = {'count': 0, 'last_seen': error.timestamp}
            error_counts[key]['count'] += 1
            if error.timestamp > error_counts[key]['last_seen']:
                error_counts[key]['last_seen'] = error.timestamp
        
        # 按出现次数排序
        sorted_errors = sorted(
            error_counts.items(),
            key=lambda x: x[1]['count'],
            reverse=True
        )
        
        return [
            {
                'error': error,
                'count': data['count'],
                'last_seen': data['last_seen'].isoformat()
            }
            for error, data in sorted_errors[:limit]
        ]
    
    def clear_error_history(self):
        """清空错误历史记录"""
        self.error_history.clear()
        self.error_counts.clear()
        self.last_error_time.clear()
        logger.info("错误历史记录已清空")


# 装饰器函数
def retry_on_error(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    error_types: Optional[List[ErrorType]] = None
):
    """重试装饰器"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            retry_config = RetryConfig(
                max_attempts=max_attempts,
                base_delay=base_delay,
                max_delay=max_delay,
                exponential_base=exponential_base,
                jitter=jitter
            )
            
            error_handler = get_error_handler()
            return await error_handler.execute_with_retry(
                func, *args, retry_config=retry_config, **kwargs
            )
        return wrapper
    return decorator


def handle_errors(error_type: Optional[ErrorType] = None, severity: Optional[ErrorSeverity] = None):
    """错误处理装饰器"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                if asyncio.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                else:
                    return func(*args, **kwargs)
            except Exception as e:
                error_handler = get_error_handler()
                await error_handler.handle_exception(
                    e,
                    context={'function': func.__name__}
                )
                raise
        return wrapper
    return decorator


# 全局错误处理器实例
_error_handler = None

def get_error_handler() -> ErrorHandler:
    """获取全局错误处理器实例"""
    global _error_handler
    if _error_handler is None:
        _error_handler = ErrorHandler()
    return _error_handler


# 便捷函数
async def log_error(
    error_type: ErrorType,
    severity: ErrorSeverity,
    message: str,
    exception: Optional[Exception] = None,
    context: Optional[Dict[str, Any]] = None
) -> ErrorInfo:
    """记录错误的便捷函数"""
    error_handler = get_error_handler()
    return error_handler.log_error(error_type, severity, message, exception, context)


async def handle_exception(
    exception: Exception,
    context: Optional[Dict[str, Any]] = None,
    custom_message: Optional[str] = None
) -> ErrorInfo:
    """处理异常的便捷函数"""
    error_handler = get_error_handler()
    return await error_handler.handle_exception(exception, context, custom_message)


if __name__ == "__main__":
    # 测试代码
    async def test_error_handler():
        print("开始测试错误处理器...")
        
        error_handler = ErrorHandler()
        
        # 测试错误记录
        print("\n1. 测试错误记录...")
        error_info = error_handler.log_error(
            ErrorType.NETWORK_ERROR,
            ErrorSeverity.MEDIUM,
            "测试网络错误",
            context={'url': 'https://example.com'}
        )
        print(f"记录的错误: {error_info}")
        
        # 测试异常处理
        print("\n2. 测试异常处理...")
        try:
            raise ConnectionError("连接失败")
        except Exception as e:
            error_info = await error_handler.handle_exception(e)
            print(f"处理的异常: {error_info}")
        
        # 测试重试机制
        print("\n3. 测试重试机制...")
        
        @retry_on_error(max_attempts=3, base_delay=0.1)
        async def failing_function():
            print("尝试执行可能失败的函数...")
            if random.random() < 0.7:  # 70%的概率失败
                raise ValueError("随机失败")
            return "成功!"
        
        try:
            result = await failing_function()
            print(f"函数执行结果: {result}")
        except Exception as e:
            print(f"函数最终失败: {e}")
        
        # 测试统计信息
        print("\n4. 测试统计信息...")
        stats = error_handler.get_error_stats()
        print(f"错误统计: {stats}")
        
        print("\n错误处理器测试完成！")
    
    asyncio.run(test_error_handler())