#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缓存管理模块
提供内存缓存和可选的Redis缓存功能
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Union
from dataclasses import asdict
import hashlib

from models import CacheEntry
from config import get_config

try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    redis = None

logger = logging.getLogger(__name__)

class CacheManager:
    """缓存管理器类"""
    
    def __init__(self):
        self.config = get_config()
        self.memory_cache: Dict[str, CacheEntry] = {}
        self.redis_client = None
        self.use_redis = False
        
        # 缓存配置
        self.max_memory_entries = self.config.cache.max_memory_entries
        self.default_expire_seconds = self.config.cache.default_expire
        self.cleanup_interval = 600  # 10分钟清理间隔
        
        # 启动清理任务
        asyncio.create_task(self._periodic_cleanup())
    
    async def initialize_redis(self):
        """初始化Redis连接"""
        if not REDIS_AVAILABLE:
            logger.warning("Redis不可用，使用内存缓存")
            return
        
        if not self.config.cache.redis_url:
            logger.info("未配置Redis URL，使用内存缓存")
            return
        
        try:
            self.redis_client = redis.from_url(
                self.config.cache.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5
            )
            
            # 测试连接
            await self.redis_client.ping()
            self.use_redis = True
            logger.info("Redis连接成功")
            
        except Exception as e:
            logger.error(f"Redis连接失败: {e}")
            self.redis_client = None
            self.use_redis = False
    
    async def get(self, key: str) -> Optional[Any]:
        """获取缓存数据"""
        try:
            # 生成缓存键
            cache_key = self._generate_cache_key(key)
            
            # 优先从Redis获取
            if self.use_redis and self.redis_client:
                try:
                    cached_data = await self.redis_client.get(cache_key)
                    if cached_data:
                        data = json.loads(cached_data)
                        logger.debug(f"从Redis获取缓存: {key}")
                        return data
                except Exception as e:
                    logger.error(f"从Redis获取缓存失败: {e}")
            
            # 从内存缓存获取
            if cache_key in self.memory_cache:
                entry = self.memory_cache[cache_key]
                
                # 检查是否过期
                if entry.expires_at and datetime.now() > entry.expires_at:
                    del self.memory_cache[cache_key]
                    logger.debug(f"内存缓存已过期: {key}")
                    return None
                
                # 更新访问时间
                entry.last_accessed = datetime.now()
                entry.access_count += 1
                
                logger.debug(f"从内存获取缓存: {key}")
                return entry.data
            
            return None
            
        except Exception as e:
            logger.error(f"获取缓存时出错: {e}")
            return None
    
    async def set(self, key: str, data: Any, expire_seconds: Optional[int] = None) -> bool:
        """设置缓存数据"""
        try:
            cache_key = self._generate_cache_key(key)
            expire_seconds = expire_seconds or self.default_expire_seconds
            expires_at = datetime.now() + timedelta(seconds=expire_seconds)
            
            # 序列化数据
            serialized_data = json.dumps(data, ensure_ascii=False, default=str)
            
            # 存储到Redis
            if self.use_redis and self.redis_client:
                try:
                    await self.redis_client.setex(
                        cache_key,
                        expire_seconds,
                        serialized_data
                    )
                    logger.debug(f"数据已存储到Redis: {key}")
                except Exception as e:
                    logger.error(f"存储到Redis失败: {e}")
            
            # 存储到内存缓存
            entry = CacheEntry(
                key=cache_key,
                data=data,
                created_at=datetime.now(),
                expires_at=expires_at,
                last_accessed=datetime.now(),
                access_count=1,
                size_bytes=len(serialized_data)
            )
            
            self.memory_cache[cache_key] = entry
            
            # 检查内存缓存大小限制
            await self._enforce_memory_limit()
            
            logger.debug(f"数据已存储到内存缓存: {key}")
            return True
            
        except Exception as e:
            logger.error(f"设置缓存时出错: {e}")
            return False
    
    async def delete(self, key: str) -> bool:
        """删除缓存数据"""
        try:
            cache_key = self._generate_cache_key(key)
            
            # 从Redis删除
            if self.use_redis and self.redis_client:
                try:
                    await self.redis_client.delete(cache_key)
                    logger.debug(f"从Redis删除缓存: {key}")
                except Exception as e:
                    logger.error(f"从Redis删除缓存失败: {e}")
            
            # 从内存缓存删除
            if cache_key in self.memory_cache:
                del self.memory_cache[cache_key]
                logger.debug(f"从内存删除缓存: {key}")
            
            return True
            
        except Exception as e:
            logger.error(f"删除缓存时出错: {e}")
            return False
    
    async def clear(self) -> bool:
        """清空所有缓存"""
        try:
            # 清空Redis缓存
            if self.use_redis and self.redis_client:
                try:
                    # 只删除我们的缓存键（以前缀区分）
                    pattern = "football_bot:*"
                    keys = await self.redis_client.keys(pattern)
                    if keys:
                        await self.redis_client.delete(*keys)
                    logger.info(f"清空Redis缓存: {len(keys)} 个键")
                except Exception as e:
                    logger.error(f"清空Redis缓存失败: {e}")
            
            # 清空内存缓存
            cache_count = len(self.memory_cache)
            self.memory_cache.clear()
            logger.info(f"清空内存缓存: {cache_count} 个条目")
            
            return True
            
        except Exception as e:
            logger.error(f"清空缓存时出错: {e}")
            return False
    
    async def exists(self, key: str) -> bool:
        """检查缓存是否存在"""
        try:
            cache_key = self._generate_cache_key(key)
            
            # 检查Redis
            if self.use_redis and self.redis_client:
                try:
                    exists = await self.redis_client.exists(cache_key)
                    if exists:
                        return True
                except Exception as e:
                    logger.error(f"检查Redis缓存存在性失败: {e}")
            
            # 检查内存缓存
            if cache_key in self.memory_cache:
                entry = self.memory_cache[cache_key]
                # 检查是否过期
                if entry.expires_at and datetime.now() > entry.expires_at:
                    del self.memory_cache[cache_key]
                    return False
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"检查缓存存在性时出错: {e}")
            return False
    
    async def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        try:
            stats = {
                'memory_entries': len(self.memory_cache),
                'redis_enabled': self.use_redis,
                'redis_connected': bool(self.redis_client),
                'max_memory_entries': self.max_memory_entries,
                'default_expire_seconds': self.default_expire_seconds,
                'status': 'healthy'
            }
            
            # 内存缓存统计
            if self.memory_cache:
                total_size = sum(entry.size_bytes for entry in self.memory_cache.values())
                total_access = sum(entry.access_count for entry in self.memory_cache.values())
                
                stats.update({
                    'memory_total_size_bytes': total_size,
                    'memory_total_access_count': total_access,
                    'memory_avg_size_bytes': total_size / len(self.memory_cache),
                    'memory_avg_access_count': total_access / len(self.memory_cache)
                })
            
            # Redis统计
            if self.use_redis and self.redis_client:
                try:
                    redis_info = await self.redis_client.info('memory')
                    stats.update({
                        'redis_memory_used': redis_info.get('used_memory_human', 'N/A'),
                        'redis_memory_peak': redis_info.get('used_memory_peak_human', 'N/A')
                    })
                except Exception as e:
                    logger.error(f"获取Redis统计信息失败: {e}")
                    stats['redis_error'] = str(e)
            
            return stats
            
        except Exception as e:
            logger.error(f"获取缓存统计信息时出错: {e}")
            return {'status': 'error', 'error': str(e)}
    
    async def get_keys(self, pattern: str = "*") -> List[str]:
        """获取缓存键列表"""
        try:
            keys = []
            
            # 从内存缓存获取键
            memory_keys = [
                key.replace("football_bot:", "")
                for key in self.memory_cache.keys()
                if pattern == "*" or pattern in key
            ]
            keys.extend(memory_keys)
            
            # 从Redis获取键
            if self.use_redis and self.redis_client:
                try:
                    redis_pattern = f"football_bot:{pattern}"
                    redis_keys = await self.redis_client.keys(redis_pattern)
                    redis_keys = [
                        key.replace("football_bot:", "")
                        for key in redis_keys
                    ]
                    keys.extend(redis_keys)
                except Exception as e:
                    logger.error(f"从Redis获取键列表失败: {e}")
            
            # 去重并排序
            return sorted(list(set(keys)))
            
        except Exception as e:
            logger.error(f"获取缓存键列表时出错: {e}")
            return []
    
    def _generate_cache_key(self, key: str) -> str:
        """生成缓存键"""
        # 添加前缀和哈希
        key_hash = hashlib.md5(key.encode()).hexdigest()[:8]
        return f"football_bot:{key}_{key_hash}"
    
    async def _enforce_memory_limit(self):
        """强制执行内存缓存大小限制"""
        try:
            if len(self.memory_cache) <= self.max_memory_entries:
                return
            
            # 按最后访问时间排序，删除最旧的条目
            sorted_entries = sorted(
                self.memory_cache.items(),
                key=lambda x: x[1].last_accessed
            )
            
            # 删除超出限制的条目
            entries_to_remove = len(self.memory_cache) - self.max_memory_entries
            for i in range(entries_to_remove):
                key_to_remove = sorted_entries[i][0]
                del self.memory_cache[key_to_remove]
            
            logger.info(f"清理内存缓存: 删除了 {entries_to_remove} 个条目")
            
        except Exception as e:
            logger.error(f"强制执行内存限制时出错: {e}")
    
    async def _periodic_cleanup(self):
        """定期清理过期缓存"""
        while True:
            try:
                await asyncio.sleep(self.cleanup_interval)
                await self._cleanup_expired_entries()
            except Exception as e:
                logger.error(f"定期清理任务出错: {e}")
                await asyncio.sleep(60)  # 出错时等待1分钟再重试
    
    async def _cleanup_expired_entries(self):
        """清理过期的内存缓存条目"""
        try:
            now = datetime.now()
            expired_keys = []
            
            for key, entry in self.memory_cache.items():
                if entry.expires_at and now > entry.expires_at:
                    expired_keys.append(key)
            
            for key in expired_keys:
                del self.memory_cache[key]
            
            if expired_keys:
                logger.info(f"清理过期缓存: 删除了 {len(expired_keys)} 个条目")
            
        except Exception as e:
            logger.error(f"清理过期缓存时出错: {e}")
    
    async def close(self):
        """关闭缓存管理器"""
        try:
            if self.redis_client:
                await self.redis_client.close()
                logger.info("Redis连接已关闭")
        except Exception as e:
            logger.error(f"关闭缓存管理器时出错: {e}")
    
    async def cleanup(self):
        """清理缓存管理器（close方法的别名）"""
        await self.close()
    
    async def initialize(self):
        """初始化缓存管理器"""
        try:
            # 初始化Redis连接（如果配置了）
            if self.use_redis:
                await self.initialize_redis()
                logger.info("Redis缓存初始化完成")
            else:
                logger.info("使用内存缓存模式")
            
            # 启动定期清理任务
            asyncio.create_task(self._periodic_cleanup())
            logger.info("缓存管理器初始化完成")
            
        except Exception as e:
            logger.error(f"初始化缓存管理器时出错: {e}")
            raise


# 全局缓存管理器实例
_cache_manager = None

def get_cache_manager() -> CacheManager:
    """获取全局缓存管理器实例"""
    global _cache_manager
    if _cache_manager is None:
        _cache_manager = CacheManager()
    return _cache_manager


# 便捷函数
async def cache_get(key: str) -> Optional[Any]:
    """获取缓存数据的便捷函数"""
    cache_manager = get_cache_manager()
    return await cache_manager.get(key)


async def cache_set(key: str, data: Any, expire_seconds: Optional[int] = None) -> bool:
    """设置缓存数据的便捷函数"""
    cache_manager = get_cache_manager()
    return await cache_manager.set(key, data, expire_seconds)


async def cache_delete(key: str) -> bool:
    """删除缓存数据的便捷函数"""
    cache_manager = get_cache_manager()
    return await cache_manager.delete(key)


async def cache_clear() -> bool:
    """清空所有缓存的便捷函数"""
    cache_manager = get_cache_manager()
    return await cache_manager.clear()


if __name__ == "__main__":
    # 测试代码
    async def test_cache():
        print("开始测试缓存管理器...")
        
        cache_manager = CacheManager()
        await cache_manager.initialize_redis()
        
        # 测试基本操作
        test_data = {"message": "Hello, World!", "timestamp": datetime.now().isoformat()}
        
        print("\n1. 设置缓存...")
        success = await cache_manager.set("test_key", test_data, 60)
        print(f"设置结果: {success}")
        
        print("\n2. 获取缓存...")
        cached_data = await cache_manager.get("test_key")
        print(f"获取结果: {cached_data}")
        
        print("\n3. 检查存在性...")
        exists = await cache_manager.exists("test_key")
        print(f"存在性: {exists}")
        
        print("\n4. 获取统计信息...")
        stats = await cache_manager.get_stats()
        print(f"统计信息: {json.dumps(stats, indent=2, ensure_ascii=False)}")
        
        print("\n5. 获取键列表...")
        keys = await cache_manager.get_keys()
        print(f"键列表: {keys}")
        
        print("\n6. 删除缓存...")
        deleted = await cache_manager.delete("test_key")
        print(f"删除结果: {deleted}")
        
        print("\n7. 验证删除...")
        cached_data_after_delete = await cache_manager.get("test_key")
        print(f"删除后获取结果: {cached_data_after_delete}")
        
        await cache_manager.close()
        print("\n缓存管理器测试完成！")
    
    asyncio.run(test_cache())