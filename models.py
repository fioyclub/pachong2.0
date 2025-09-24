#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据模型定义
定义足球赛事爬虫机器人的核心数据结构
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List
import json
from enum import Enum


class MatchStatus(Enum):
    """比赛状态枚举"""
    UPCOMING = "upcoming"  # 即将开始
    LIVE = "live"  # 进行中
    FINISHED = "finished"  # 已结束
    CANCELLED = "cancelled"  # 已取消


class SystemComponentStatus(Enum):
    """系统组件状态枚举"""
    HEALTHY = "healthy"  # 健康
    WARNING = "warning"  # 警告
    ERROR = "error"  # 错误
    MAINTENANCE = "maintenance"  # 维护中


@dataclass
class MatchData:
    """足球比赛数据模型"""
    match_id: str  # 比赛唯一标识
    start_time: datetime  # 比赛开始时间（马来西亚时区）
    home_team: str  # 主队名称
    away_team: str  # 客队名称
    odds_1: float  # 主胜赔率
    odds_x: float  # 平局赔率
    odds_2: float  # 客胜赔率
    league: Optional[str] = None  # 联赛名称
    status: MatchStatus = MatchStatus.UPCOMING  # 比赛状态
    created_at: Optional[datetime] = field(default_factory=datetime.now)  # 创建时间
    updated_at: Optional[datetime] = field(default_factory=datetime.now)  # 更新时间
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            'match_id': self.match_id,
            'start_time': self.start_time.isoformat(),
            'home_team': self.home_team,
            'away_team': self.away_team,
            'odds_1': self.odds_1,
            'odds_x': self.odds_x,
            'odds_2': self.odds_2,
            'league': self.league,
            'status': self.status.value,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MatchData':
        """从字典创建对象"""
        return cls(
            match_id=data['match_id'],
            start_time=datetime.fromisoformat(data['start_time']),
            home_team=data['home_team'],
            away_team=data['away_team'],
            odds_1=float(data['odds_1']),
            odds_x=float(data['odds_x']),
            odds_2=float(data['odds_2']),
            league=data.get('league'),
            status=MatchStatus(data.get('status', 'upcoming')),
            created_at=datetime.fromisoformat(data['created_at']) if data.get('created_at') else None,
            updated_at=datetime.fromisoformat(data['updated_at']) if data.get('updated_at') else None
        )
    
    def format_for_telegram(self) -> str:
        """格式化为Telegram消息"""
        time_str = self.start_time.strftime('%Y-%m-%d %H:%M')
        return (
            f"⚽ {self.home_team} 🆚 {self.away_team}\n"
            f"🕐 {time_str} (MY时间)\n"
            f"📊 赔率: 主胜{self.odds_1} | 平局{self.odds_x} | 客胜{self.odds_2}\n"
            f"🏆 {self.league or '未知联赛'}\n"
        )


@dataclass
class UserSession:
    """用户会话数据模型"""
    user_id: str  # Telegram用户ID
    chat_id: str  # 聊天ID
    last_active: datetime  # 最后活跃时间
    preferences: Dict[str, Any] = field(default_factory=dict)  # 用户偏好设置
    command_history: List[str] = field(default_factory=list)  # 命令历史
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            'user_id': self.user_id,
            'chat_id': self.chat_id,
            'last_active': self.last_active.isoformat(),
            'preferences': self.preferences,
            'command_history': self.command_history
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'UserSession':
        """从字典创建对象"""
        return cls(
            user_id=data['user_id'],
            chat_id=data['chat_id'],
            last_active=datetime.fromisoformat(data['last_active']),
            preferences=data.get('preferences', {}),
            command_history=data.get('command_history', [])
        )
    
    def update_activity(self):
        """更新最后活跃时间"""
        self.last_active = datetime.now()
    
    def add_command(self, command: str):
        """添加命令到历史记录"""
        self.command_history.append(command)
        # 只保留最近20条命令
        if len(self.command_history) > 20:
            self.command_history = self.command_history[-20:]


@dataclass
class SystemStatus:
    """系统状态数据模型"""
    component: str  # 组件名称
    status: SystemComponentStatus  # 组件状态
    last_check: datetime  # 最后检查时间
    metadata: Dict[str, Any] = field(default_factory=dict)  # 元数据
    error_message: Optional[str] = None  # 错误信息
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            'component': self.component,
            'status': self.status.value,
            'last_check': self.last_check.isoformat(),
            'metadata': self.metadata,
            'error_message': self.error_message
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SystemStatus':
        """从字典创建对象"""
        return cls(
            component=data['component'],
            status=SystemComponentStatus(data['status']),
            last_check=datetime.fromisoformat(data['last_check']),
            metadata=data.get('metadata', {}),
            error_message=data.get('error_message')
        )
    
    def is_healthy(self) -> bool:
        """检查组件是否健康"""
        return self.status == SystemComponentStatus.HEALTHY
    
    def update_status(self, status: SystemComponentStatus, error_message: Optional[str] = None):
        """更新组件状态"""
        self.status = status
        self.last_check = datetime.now()
        self.error_message = error_message


@dataclass
class CacheEntry:
    """缓存条目数据模型"""
    key: str  # 缓存键
    data: Any  # 缓存数据
    created_at: datetime  # 创建时间
    expires_at: Optional[datetime] = None  # 过期时间
    last_accessed: datetime = field(default_factory=datetime.now)  # 最后访问时间
    access_count: int = 0  # 访问次数
    size_bytes: int = 0  # 数据大小（字节）
    
    def is_expired(self) -> bool:
        """检查是否过期"""
        if self.expires_at is None:
            return False
        return datetime.now() > self.expires_at
    
    def access(self) -> Any:
        """访问缓存数据"""
        self.access_count += 1
        self.last_accessed = datetime.now()
        return self.data


# 配置常量
CACHE_CONFIG = {
    'matches_cache_ttl': 300,  # 5分钟
    'max_cache_size': 1000,    # 最大缓存条目
    'cleanup_interval': 600    # 10分钟清理一次
}

DEFAULT_SETTINGS = {
    'max_matches': 12,
    'timezone': 'Asia/Kuala_Lumpur',
    'retry_attempts': 3,
    'request_timeout': 30,
    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

# 系统组件列表
SYSTEM_COMPONENTS = [
    'telegram_bot',
    'web_scraper',
    'cache_manager',
    'data_processor',
    'health_monitor'
]