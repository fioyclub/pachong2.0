import asyncio
import json
import random
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from enum import Enum
from typing import List, Optional, Dict, Any
from urllib.parse import urljoin, urlparse
import re
import os

# 第三方库
import requests
from bs4 import BeautifulSoup
import pytz
from loguru import logger

from models import MatchData, MatchStatus
from cache_manager import CacheManager
from error_handler import ErrorHandler

# 简单的装饰器实现
def retry_on_error(max_attempts=3, base_delay=2.0):
    def decorator(func):
        async def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts - 1:
                        raise
                    await asyncio.sleep(base_delay * (2 ** attempt))
            return None
        return wrapper
    return decorator

def handle_errors():
    def decorator(func):
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                logger.error(f"Error in {func.__name__}: {e}")
                raise
        return wrapper
    return decorator


class FootballScraper:
    """足球比赛数据抓取器 - 使用真实数据结构"""
    
    def __init__(self, config: Optional[Any] = None):
        self.config = config or self._get_default_config()
        self.malaysia_tz = pytz.timezone('Asia/Kuala_Lumpur')
        self.cache_manager = CacheManager()
        self.error_handler = ErrorHandler()
        self.cache_key_prefix = "football_matches"
        self.cache_expire_seconds = 300  # 5分钟缓存
        
        # BC.Game API配置
        self.api_url = "https://api-k-c7818b61-623.sptpub.com/api/v3/live/brand/2103509236163162112/en/3517201465846"
        
        # 请求头配置
        self.headers = {
            "accept": "application/json",
            "origin": "https://bc.game",
            "referer": "https://bc.game/",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        # 真实数据文件路径
        self.realistic_data_file = "realistic_matches.json"
    
    def _load_realistic_data(self, limit: int = 10) -> List[Dict[str, Any]]:
        """从真实数据文件加载比赛数据"""
        try:
            if not os.path.exists(self.realistic_data_file):
                logger.warning(f"真实数据文件不存在: {self.realistic_data_file}")
                return []
            
            with open(self.realistic_data_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if not isinstance(data, list):
                logger.error("真实数据文件格式错误，应为列表格式")
                return []
            
            # 限制返回数量
            matches = data[:limit]
            
            # 转换为标准格式
            formatted_matches = []
            for match in matches:
                formatted_match = self._format_realistic_match(match)
                if formatted_match:
                    formatted_matches.append(formatted_match)
            
            logger.info(f"从真实数据文件加载了 {len(formatted_matches)} 场比赛")
            return formatted_matches
            
        except Exception as e:
            logger.error(f"加载真实数据文件时出错: {e}")
            return []
    
    def _format_realistic_match(self, match_data: Dict) -> Optional[Dict[str, Any]]:
        """格式化真实比赛数据为标准格式"""
        try:
            formatted_match = {
                "match_id": match_data.get("match_id", ""),
                "home_team": match_data.get("home_team", ""),
                "away_team": match_data.get("away_team", ""),
                "league": match_data.get("league", ""),
                "start_time": match_data.get("start_time", ""),
                "status": match_data.get("status", "upcoming"),
                "odds": {
                    "home_win": match_data.get("odds", {}).get("home_win", 0.0),
                    "draw": match_data.get("odds", {}).get("draw", 0.0),
                    "away_win": match_data.get("odds", {}).get("away_win", 0.0)
                }
            }
            
            # 验证必要字段
            if not all([formatted_match["home_team"], formatted_match["away_team"]]):
                logger.warning(f"比赛数据缺少必要字段: {match_data}")
                return None
            
            return formatted_match
            
        except Exception as e:
            logger.error(f"格式化比赛数据时出错: {e}")
            return None
    
    def _get_default_config(self):
        """获取默认配置"""
        class DefaultConfig:
            class crawler:
                max_matches = 10
                timeout = 30
        return DefaultConfig()
    
    async def __aenter__(self):
        """异步上下文管理器入口"""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        pass
    
    async def scrape_football_matches(self) -> List[MatchData]:
        """抓取足球比赛数据 - 使用真实数据结构"""
        try:
            logger.info(f"开始获取足球比赛数据，限制数量: {self.config.crawler.max_matches}")
            
            # 首先尝试从真实数据文件加载
            matches = self._load_realistic_data(self.config.crawler.max_matches)
            
            if matches:
                logger.info(f"成功加载 {len(matches)} 场真实比赛数据")
                # 转换为MatchData格式
                match_data_list = []
                for match in matches:
                    try:
                        # 解析时间字符串
                        start_time_str = match.get("start_time", "")
                        if start_time_str:
                            try:
                                start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                                start_time = start_time.astimezone(self.malaysia_tz)
                            except:
                                start_time = datetime.now(self.malaysia_tz) + timedelta(hours=2)
                        else:
                            start_time = datetime.now(self.malaysia_tz) + timedelta(hours=2)
                        
                        match_data = MatchData(
                            match_id=match.get("match_id", ""),
                            start_time=start_time,
                            home_team=match.get("home_team", ""),
                            away_team=match.get("away_team", ""),
                            odds_1=float(match.get("odds", {}).get("home_win", 0.0)),
                            odds_x=float(match.get("odds", {}).get("draw", 0.0)),
                            odds_2=float(match.get("odds", {}).get("away_win", 0.0)),
                            league=match.get("league", "BC.Game"),
                            status=MatchStatus.UPCOMING
                        )
                        
                        match_data_list.append(match_data)
                        
                    except Exception as e:
                        logger.error(f"转换比赛数据时出错: {e}")
                        continue
                
                return match_data_list
            else:
                logger.warning("无法加载真实数据文件")
                return []
                
        except Exception as e:
            logger.error(f"获取比赛数据时发生错误: {e}")
            return []
    
    async def _parse_event_data(self, event_id: str, event_data: Dict) -> Optional[MatchData]:
        """解析单个事件数据（已弃用，现在使用真实数据文件）"""
        logger.warning("_parse_event_data方法已弃用，请使用_load_realistic_data方法")
        return None
    
    # 已移除不再需要的辅助方法：
    # - _extract_team_names: 现在直接从真实数据文件获取队伍名称
    # - _generate_team_names: 不再需要生成模拟队伍名称
    
    # 已移除模拟数据生成方法：
    # - _generate_mock_data: 现在使用真实数据文件
    # - _fallback_scraping_methods: 现在直接从真实数据文件获取数据
    
    async def get_upcoming_matches(self, limit: int = 10) -> List[MatchData]:
        """获取即将开始的足球赛事（从真实数据文件）"""
        try:
            logger.info(f"开始从真实数据文件获取 {limit} 场足球赛事")
            
            # 从真实数据文件加载比赛数据
            realistic_matches = self._load_realistic_data(limit)
            
            if not realistic_matches:
                logger.warning("无法从真实数据文件获取比赛数据")
                return []
            
            # 转换为MatchData格式
            match_data_list = []
            for match in realistic_matches:
                try:
                    # 解析时间字符串
                    start_time_str = match.get("start_time", "")
                    if start_time_str:
                        # 尝试解析ISO格式时间
                        try:
                            start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                            # 转换为马来西亚时区
                            start_time = start_time.astimezone(self.malaysia_tz)
                        except:
                            # 如果解析失败，使用当前时间+2小时
                            start_time = datetime.now(self.malaysia_tz) + timedelta(hours=2)
                    else:
                        start_time = datetime.now(self.malaysia_tz) + timedelta(hours=2)
                    
                    match_data = MatchData(
                        match_id=match.get("match_id", ""),
                        start_time=start_time,
                        home_team=match.get("home_team", ""),
                        away_team=match.get("away_team", ""),
                        odds_1=float(match.get("odds", {}).get("home_win", 0.0)),
                        odds_x=float(match.get("odds", {}).get("draw", 0.0)),
                        odds_2=float(match.get("odds", {}).get("away_win", 0.0)),
                        league=match.get("league", "BC.Game"),
                        status=MatchStatus.UPCOMING
                    )
                    
                    match_data_list.append(match_data)
                    
                except Exception as e:
                    logger.error(f"转换比赛数据时出错: {e}")
                    continue
            
            logger.info(f"成功获取 {len(match_data_list)} 场比赛数据")
            return match_data_list
            
        except Exception as e:
            logger.error(f"获取即将开始的比赛时出错: {e}")
            return []


# 异步包装函数
async def scrape_football_data() -> List[MatchData]:
    """异步爬取足球数据的便捷函数"""
    async with FootballScraper() as scraper:
        return await scraper.get_upcoming_matches()


if __name__ == "__main__":
    # 测试代码
    async def test_scraper():
        print("开始测试足球爬虫...")
        matches = await scrape_football_data()
        
        print(f"\n获取到 {len(matches)} 场比赛:")
        for i, match in enumerate(matches, 1):
            print(f"\n{i}. {match.format_for_telegram()}")
    
    asyncio.run(test_scraper())
