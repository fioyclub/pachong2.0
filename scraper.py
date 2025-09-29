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

try:
    from models import MatchData, MatchStatus
    from cache_manager import CacheManager
    from error_handler import ErrorHandler
except ImportError:
    # 在部署环境中，尝试相对导入
    try:
        from .models import MatchData, MatchStatus
        from .cache_manager import CacheManager
        from .error_handler import ErrorHandler
    except ImportError:
        # 如果都失败了，创建占位符类
        from enum import Enum
        from dataclasses import dataclass
        from datetime import datetime
        from typing import Optional
        
        class MatchStatus(Enum):
            UPCOMING = "upcoming"
            LIVE = "live"
            FINISHED = "finished"
        
        @dataclass
        class MatchData:
            home_team: str = ""
            away_team: str = ""
            match_time: Optional[datetime] = None
            league: str = ""
            odds_1: str = ""
            odds_x: str = ""
            odds_2: str = ""
            status: MatchStatus = MatchStatus.UPCOMING
            
            def format_for_telegram(self) -> str:
                return f"{self.home_team} vs {self.away_team}"
        
        class CacheManager:
            def __init__(self):
                pass
            def get(self, key): return None
            def set(self, key, value, expire=None): pass
            def _start_cleanup_task(self): pass
        
        class ErrorHandler:
            def __init__(self):
                pass
try:
    from api_updater import APIEndpointUpdater
except ImportError:
    # 在部署环境中，尝试相对导入
    try:
        from .api_updater import APIEndpointUpdater
    except ImportError:
        # 如果都失败了，创建一个空的类作为占位符
        class APIEndpointUpdater:
            def __init__(self, *args, **kwargs):
                pass
            def update_endpoints(self, *args, **kwargs):
                return False

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
        
        # 初始化API端点更新器
        self.api_updater = APIEndpointUpdater()
        
        # 确保缓存管理器清理任务启动（如果有事件循环）
        self._ensure_cache_cleanup()
        
        # BC.Game API配置 - 使用新发现的有效端点
        self.api_endpoints = self._load_api_config()
        
        # 备用端点（如果配置文件不存在）
        if not self.api_endpoints:
            self.api_endpoints = [
                "https://bc.game/cache/platform-sports/v14/live10/2103509236163162112/en/",
                "https://bc.game/cache/platform-sports/v14/prematch/2103509236163162112/en/",
                "https://bc.game/cache/platform-sports/v14/live/2103509236163162112/en/"
            ]
        
        # 请求头配置 - 模拟真实浏览器请求
        self.headers = {
            "accept": "application/json, text/plain, */*",
            "accept-encoding": "gzip, deflate, br, zstd",
            "accept-language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "cache-control": "no-cache",
            "origin": "https://bc.game",
            "pragma": "no-cache",
            "referer": "https://bc.game/",
            "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        }
        
        # 体育项目映射
        self.sports_mapping = {}
        self.categories_mapping = {}
        self.tournaments_mapping = {}
    
    def _ensure_cache_cleanup(self):
        """确保缓存管理器清理任务启动"""
        try:
            # 尝试启动清理任务（如果有事件循环）
            self.cache_manager._start_cleanup_task()
        except Exception as e:
            # 忽略错误，清理任务将在需要时启动
            pass
    
    def _load_api_config(self) -> List[str]:
        """从配置文件加载API端点"""
        try:
            import json
            import os
            
            config_file = os.path.join(os.path.dirname(__file__), 'api_config.json')
            if os.path.exists(config_file):
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    
                endpoints = []
                if 'primary_endpoint' in config:
                    endpoints.append(config['primary_endpoint'])
                if 'backup_endpoints' in config:
                    endpoints.extend(config['backup_endpoints'])
                    
                logger.info(f"从配置文件加载了 {len(endpoints)} 个API端点")
                return endpoints
                
        except Exception as e:
            logger.warning(f"加载API配置失败: {e}")
            
        return []
    
    def _fetch_api_data(self, url: str) -> Optional[Dict[str, Any]]:
        """从BC.Game API获取数据"""
        try:
            logger.info(f"正在请求API: {url}")
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                logger.info(f"API请求成功，状态码: {response.status_code}")
                return data
            else:
                logger.warning(f"API请求失败，状态码: {response.status_code}")
                
                # 如果API请求失败，尝试自动更新端点
                if response.status_code in [503, 404, 500]:
                    logger.info("检测到API端点可能失效，尝试自动更新...")
                    self._try_update_endpoints()
                
                return None
                
        except Exception as e:
            logger.error(f"API请求出错: {e}")
            return None
    
    def _try_update_endpoints(self):
        """尝试更新API端点"""
        try:
            updated = self.api_updater.check_and_update_endpoints()
            if updated:
                # 重新加载API配置
                new_endpoints = self._load_api_config()
                if new_endpoints:
                    self.api_endpoints = new_endpoints
                    logger.info(f"API端点已自动更新，新端点数量: {len(self.api_endpoints)}")
                else:
                    logger.warning("API端点更新后未找到有效端点")
            else:
                logger.info("API端点检查完成，无需更新")
        except Exception as e:
            logger.error(f"自动更新API端点失败: {e}")
    
    def _parse_api_response(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """解析API响应数据"""
        matches = []
        
        try:
            # 检查数据格式
            if 'data' in data and 'items' in data['data']:
                # 新API格式（直接返回比赛列表）
                matches = self._parse_direct_match_list(data)
            elif 'events' in data:
                # 旧API格式
                matches = self._parse_old_api_format(data)
            # 如果都不匹配，记录数据结构
            else:
                logger.warning(f"未知的API数据格式，数据键: {list(data.keys())}")
                return []
            
        except Exception as e:
            logger.error(f"解析API响应时出错: {e}")
            return []
        
        return matches
    
    def _parse_direct_match_list(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """解析直接返回比赛列表的新API格式"""
        matches = []
        
        try:
            items = data.get('data', {}).get('items', [])
            logger.info(f"API返回 {len(items)} 个比赛项目")
            
            for item in items:
                # 获取体育项目信息
                sport_info = item.get('sportInfo', {})
                sport_name = sport_info.get('name', '')
                
                # 只处理足球赛事（包括eSoccer）
                if sport_name.lower() not in ['soccer', 'esoccer']:
                    continue
                
                # 获取比赛信息
                match_info = item.get('matchInfo', {})
                if not match_info:
                    continue
                
                match = self._parse_direct_match_info(match_info, item)
                if match:
                    matches.append(match)
            
            logger.info(f"从新API格式成功解析 {len(matches)} 场足球比赛")
            return matches
            
        except Exception as e:
            logger.error(f"解析直接比赛列表时出错: {e}")
            return []
    
    def _parse_direct_match_info(self, match_info: Dict[str, Any], item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """解析直接比赛信息"""
        try:
            # 获取比赛描述
            desc = match_info.get('desc', {})
            if not desc:
                return None
            
            # 获取比赛ID
            match_id = match_info.get('id', '')
            if not match_id:
                return None
            
            # 获取比赛时间
            scheduled = desc.get('scheduled')
            if not scheduled:
                return None
            
            # 获取参赛队伍
            competitors = desc.get('competitors', [])
            if len(competitors) < 2:
                return None
            
            home_team = competitors[0].get('name', '') if len(competitors) > 0 else ''
            away_team = competitors[1].get('name', '') if len(competitors) > 1 else ''
            
            if not home_team or not away_team:
                return None
            
            # 获取联赛信息
            tournament_info = item.get('tournamentInfo', {})
            category_info = item.get('categoryInfo', {})
            sport_info = item.get('sportInfo', {})
            
            league = tournament_info.get('name', 'Unknown League')
            category = category_info.get('name', 'Unknown Category')
            sport = sport_info.get('name', 'Soccer')
            
            # 解析赔率
            markets = match_info.get('markets', {})
            odds = self._parse_direct_match_odds(markets)
            
            # 检查比赛状态
            state = match_info.get('state', {})
            match_status = state.get('match_status', 0)
            status = state.get('status', 0)
            
            # 只返回未开始的比赛（status=1表示可投注，match_status=0表示未开始）
            if status != 1:
                return None
            
            # 格式化比赛数据
            match_data = {
                "match_id": match_id,
                "home_team": home_team,
                "away_team": away_team,
                "league": league,
                "category": category,
                "sport": sport,
                "tournament": league,
                "start_time": scheduled,
                "status": "upcoming",
                "odds": odds
            }
            
            return match_data
            
        except Exception as e:
            logger.error(f"解析直接比赛信息时出错: {e}")
            return None
    
    def _parse_direct_match_odds(self, markets: Dict[str, Any]) -> Dict[str, float]:
        """解析直接比赛的赔率数据"""
        odds = {"home_win": 0.0, "draw": 0.0, "away_win": 0.0}
        
        try:
            # 查找1X2市场（市场ID通常是"1"）
            if '1' in markets:
                market_1x2 = markets['1']
                if '' in market_1x2:  # 无参数的基本市场
                    selections = market_1x2['']
                    
                    # 解析选项
                    if '1' in selections:  # 主队胜
                        odds["home_win"] = float(selections['1'].get('k', 0.0))
                    if '2' in selections:  # 平局
                        odds["draw"] = float(selections['2'].get('k', 0.0))
                    if '3' in selections:  # 客队胜
                        odds["away_win"] = float(selections['3'].get('k', 0.0))
            
            # 如果没有找到标准1X2市场，尝试其他可能的市场
            if odds["home_win"] == 0.0 and odds["away_win"] == 0.0:
                for market_id, market_data in markets.items():
                    if isinstance(market_data, dict) and '' in market_data:
                        selections = market_data['']
                        if len(selections) >= 2:
                            # 尝试按顺序解析
                            selection_keys = list(selections.keys())
                            if len(selection_keys) >= 2:
                                odds["home_win"] = float(selections[selection_keys[0]].get('k', 0.0))
                                if len(selection_keys) == 3:
                                    odds["draw"] = float(selections[selection_keys[1]].get('k', 0.0))
                                    odds["away_win"] = float(selections[selection_keys[2]].get('k', 0.0))
                                else:
                                    odds["away_win"] = float(selections[selection_keys[1]].get('k', 0.0))
                            break
            
        except Exception as e:
            logger.error(f"解析直接比赛赔率时出错: {e}")
        
        return odds
    
    def _parse_new_api_format(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """解析新API格式数据（cache/platform-sports）"""
        matches = []
        
        try:
            items = data.get('data', {}).get('items', [])
            
            for item in items:
                sport_info = item.get('sportInfo', {})
                sport_name = sport_info.get('name', '')
                
                # 只处理足球赛事
                if sport_name.lower() != 'soccer':
                    continue
                
                # 解析比赛数据
                competitions = item.get('competitions', [])
                for competition in competitions:
                    events = competition.get('events', [])
                    for event in events:
                        match = self._parse_new_event_format(event, competition)
                        if match:
                            matches.append(match)
            
            logger.info(f"从新API格式成功解析 {len(matches)} 场比赛")
            return matches
            
        except Exception as e:
            logger.error(f"解析新API格式时出错: {e}")
            return []
    
    def _parse_old_api_format(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """解析旧API格式数据"""
        matches = []
        
        try:
            # 解析体育项目映射
            if 'sports' in data:
                for sport_id, sport_info in data['sports'].items():
                    self.sports_mapping[sport_id] = sport_info
            
            # 解析分类映射
            if 'categories' in data:
                for category_id, category_info in data['categories'].items():
                    self.categories_mapping[category_id] = category_info
            
            # 解析锦标赛映射
            if 'tournaments' in data:
                for tournament_id, tournament_info in data['tournaments'].items():
                    self.tournaments_mapping[tournament_id] = tournament_info
            
            # 解析事件数据
            if 'events' in data:
                events = data['events']
                
                # 处理字典格式的events
                if isinstance(events, dict):
                    for event_id, event_data in events.items():
                        match = self._parse_single_event(event_id, event_data)
                        if match:
                            matches.append(match)
                
                # 处理列表格式的events
                elif isinstance(events, list):
                    for event_data in events:
                        if isinstance(event_data, dict) and 'id' in event_data:
                            match = self._parse_single_event(event_data['id'], event_data)
                            if match:
                                matches.append(match)
            
            logger.info(f"从旧API格式成功解析 {len(matches)} 场比赛")
            return matches
            
        except Exception as e:
            logger.error(f"解析旧API格式时出错: {e}")
            return []
    
    def _parse_new_event_format(self, event: Dict[str, Any], competition: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """解析新API格式的事件数据"""
        try:
            # 获取基本信息
            event_id = event.get('id', '')
            if not event_id:
                return None
            
            # 获取比赛时间
            start_time = event.get('startTime')
            if not start_time:
                return None
            
            # 获取参赛队伍
            competitors = event.get('competitors', [])
            if len(competitors) < 2:
                return None
            
            home_team = competitors[0].get('name', '') if len(competitors) > 0 else ''
            away_team = competitors[1].get('name', '') if len(competitors) > 1 else ''
            
            if not home_team or not away_team:
                return None
            
            # 获取联赛信息
            competition_name = competition.get('name', 'Unknown League')
            
            # 解析赔率（新API格式）
            odds = self._parse_new_event_odds(event.get('markets', []))
            
            # 格式化比赛数据
            match_data = {
                "match_id": event_id,
                "home_team": home_team,
                "away_team": away_team,
                "league": competition_name,
                "category": "Soccer",
                "sport": "Soccer",
                "tournament": competition_name,
                "start_time": start_time,
                "status": "upcoming",
                "odds": odds
            }
            
            return match_data
            
        except Exception as e:
            logger.error(f"解析新格式事件时出错: {e}")
            return None
    
    def _parse_new_event_odds(self, markets: List[Dict[str, Any]]) -> Dict[str, float]:
        """解析新API格式的赔率数据"""
        odds = {"home_win": 0.0, "draw": 0.0, "away_win": 0.0}
        
        try:
            # 查找1X2市场（胜平负）
            for market in markets:
                market_type = market.get('type', '')
                selections = market.get('selections', [])
                
                # 寻找胜平负市场
                if market_type in ['1X2', 'match_winner', 'full_time_result'] and len(selections) >= 2:
                    for i, selection in enumerate(selections):
                        odds_value = float(selection.get('odds', 0.0))
                        
                        if i == 0:  # 主队胜
                            odds["home_win"] = odds_value
                        elif i == 1 and len(selections) == 3:  # 平局（如果有3个选项）
                            odds["draw"] = odds_value
                        elif (i == 1 and len(selections) == 2) or (i == 2 and len(selections) == 3):  # 客队胜
                            odds["away_win"] = odds_value
                    
                    if odds["home_win"] > 0 and odds["away_win"] > 0:
                        break
            
        except Exception as e:
            logger.error(f"解析新格式赔率时出错: {e}")
        
        return odds
    
    def _parse_single_event(self, event_id: str, event_data: Dict) -> Optional[Dict[str, Any]]:
        """解析单个事件数据"""
        try:
            # 获取事件描述信息
            desc = event_data.get('desc', {})
            if not desc:
                return None
            
            # 获取比赛时间
            scheduled = desc.get('scheduled')
            if not scheduled:
                return None
            
            # 获取参赛队伍
            competitors = desc.get('competitors', {})
            if len(competitors) < 2:
                return None
            
            # 提取队伍名称
            teams = list(competitors.values())
            home_team = teams[0].get('name', '') if len(teams) > 0 else ''
            away_team = teams[1].get('name', '') if len(teams) > 1 else ''
            
            if not home_team or not away_team:
                return None
            
            # 获取体育项目、分类、锦标赛信息
            sport_id = desc.get('sport')
            category_id = desc.get('category')
            tournament_id = desc.get('tournament')
            
            sport_name = self.sports_mapping.get(sport_id, {}).get('name', 'Unknown')
            category_name = self.categories_mapping.get(category_id, {}).get('name', 'Unknown')
            tournament_name = self.tournaments_mapping.get(tournament_id, {}).get('name', 'Unknown')
            
            # 解析赔率
            odds = self._parse_event_odds(event_data.get('markets', {}))
            
            # 格式化比赛数据
            match_data = {
                "match_id": event_id,
                "home_team": home_team,
                "away_team": away_team,
                "league": f"{sport_name} - {tournament_name}",
                "category": category_name,
                "sport": sport_name,
                "tournament": tournament_name,
                "start_time": scheduled,
                "status": "upcoming",
                "odds": odds
            }
            
            return match_data
            
        except Exception as e:
            logger.error(f"解析事件 {event_id} 时出错: {e}")
            return None
    
    def _parse_event_odds(self, markets: Dict) -> Dict[str, float]:
        """解析事件赔率"""
        odds = {"home_win": 0.0, "draw": 0.0, "away_win": 0.0}
        
        try:
            # 查找1X2市场（胜平负）
            for market_id, market_data in markets.items():
                if isinstance(market_data, dict) and 'selections' in market_data:
                    selections = market_data['selections']
                    
                    # 如果有3个选项，通常是胜平负
                    if len(selections) == 3:
                        selection_list = list(selections.values())
                        if len(selection_list) >= 3:
                            odds["home_win"] = float(selection_list[0].get('k', 0.0))
                            odds["draw"] = float(selection_list[1].get('k', 0.0))
                            odds["away_win"] = float(selection_list[2].get('k', 0.0))
                            break
                    
                    # 如果只有2个选项，通常是主客胜负
                    elif len(selections) == 2:
                        selection_list = list(selections.values())
                        if len(selection_list) >= 2:
                            odds["home_win"] = float(selection_list[0].get('k', 0.0))
                            odds["away_win"] = float(selection_list[1].get('k', 0.0))
                            break
            
        except Exception as e:
            logger.error(f"解析赔率时出错: {e}")
        
        return odds
    
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
        """抓取足球比赛数据 - 使用真实BC.Game API"""
        try:
            logger.info(f"开始从BC.Game API获取足球比赛数据，限制数量: {self.config.crawler.max_matches}")
            
            all_matches = []
            
            # 遍历所有API端点
            for endpoint in self.api_endpoints:
                try:
                    # 获取API数据
                    api_data = self._fetch_api_data(endpoint)
                    if not api_data:
                        continue
                    
                    # 解析API响应
                    matches = self._parse_api_response(api_data)
                    all_matches.extend(matches)
                    
                    logger.info(f"从端点 {endpoint} 获取到 {len(matches)} 场比赛")
                    
                except Exception as e:
                    logger.error(f"处理API端点 {endpoint} 时出错: {e}")
                    continue
            
            # 去重并限制数量
            unique_matches = self._deduplicate_matches(all_matches)
            limited_matches = unique_matches[:self.config.crawler.max_matches]
            
            logger.info(f"总共获取到 {len(unique_matches)} 场唯一比赛，返回前 {len(limited_matches)} 场")
            
            # 转换为MatchData格式
            match_data_list = []
            for match in limited_matches:
                try:
                    match_data = self._convert_to_match_data(match)
                    if match_data:
                        match_data_list.append(match_data)
                        
                except Exception as e:
                    logger.error(f"转换比赛数据时出错: {e}")
                    continue
            
            return match_data_list
                
        except Exception as e:
            logger.error(f"获取比赛数据时发生错误: {e}")
            return []
    
    def _deduplicate_matches(self, matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """去重比赛数据"""
        seen_matches = set()
        unique_matches = []
        
        for match in matches:
            # 使用match_id作为唯一标识
            match_id = match.get('match_id', '')
            if match_id and match_id not in seen_matches:
                seen_matches.add(match_id)
                unique_matches.append(match)
        
        return unique_matches
    
    def _convert_to_match_data(self, match: Dict[str, Any]) -> Optional[MatchData]:
        """将解析的比赛数据转换为MatchData格式"""
        try:
            # 解析时间字符串
            start_time_str = match.get("start_time", "")
            if start_time_str:
                try:
                    # 处理时间戳格式
                    if isinstance(start_time_str, (int, float)):
                        start_time = datetime.fromtimestamp(start_time_str / 1000, tz=self.malaysia_tz)
                    else:
                        # 处理ISO格式时间
                        start_time = datetime.fromisoformat(str(start_time_str).replace('Z', '+00:00'))
                        start_time = start_time.astimezone(self.malaysia_tz)
                except Exception as e:
                    logger.warning(f"解析时间失败: {e}，使用默认时间")
                    start_time = datetime.now(self.malaysia_tz) + timedelta(hours=2)
            else:
                start_time = datetime.now(self.malaysia_tz) + timedelta(hours=2)
            
            # 创建MatchData对象
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
            
            return match_data
            
        except Exception as e:
            logger.error(f"转换MatchData时出错: {e}")
            return None
    
    # 已移除不再需要的辅助方法：
    # - _extract_team_names: 现在直接从真实数据文件获取队伍名称
    # - _generate_team_names: 不再需要生成模拟队伍名称
    
    # 已移除模拟数据生成方法：
    # - _generate_mock_data: 现在使用真实数据文件
    # - _fallback_scraping_methods: 现在直接从真实数据文件获取数据
    
    async def get_upcoming_matches(self, limit: int = 10) -> List[MatchData]:
        """获取即将开始的足球赛事（从BC.Game API，失败时使用备用数据）"""
        try:
            logger.info(f"开始从BC.Game API获取 {limit} 场足球赛事")
            
            # 临时设置限制
            original_limit = self.config.crawler.max_matches
            self.config.crawler.max_matches = limit
            
            # 调用主要的抓取方法
            matches = await self.scrape_football_matches()
            
            # 恢复原始限制
            self.config.crawler.max_matches = original_limit
            
            # 如果API没有返回数据，使用备用数据源
            if not matches:
                logger.warning("BC.Game API未返回数据，尝试使用备用数据源")
                matches = await self._load_fallback_data(limit)
            
            logger.info(f"成功获取 {len(matches)} 场比赛数据")
            return matches
            
        except Exception as e:
            logger.error(f"获取即将开始的比赛时出错: {e}")
            # 发生异常时也尝试使用备用数据
            try:
                logger.info("尝试使用备用数据源")
                return await self._load_fallback_data(limit)
            except Exception as fallback_error:
                logger.error(f"备用数据源也失败: {fallback_error}")
                return []


    async def _load_fallback_data(self, limit: int = 10) -> List[MatchData]:
        """加载备用数据源（realistic_matches.json）"""
        try:
            import json
            import os
            
            # 获取当前脚本目录
            current_dir = os.path.dirname(os.path.abspath(__file__))
            json_file_path = os.path.join(current_dir, 'realistic_matches.json')
            
            logger.info(f"尝试从备用数据文件加载数据: {json_file_path}")
            
            if not os.path.exists(json_file_path):
                logger.error(f"备用数据文件不存在: {json_file_path}")
                return []
            
            with open(json_file_path, 'r', encoding='utf-8') as f:
                matches_data = json.load(f)
            
            logger.info(f"从备用数据文件加载了 {len(matches_data)} 场比赛")
            
            # 转换为MatchData格式
            match_data_list = []
            for match in matches_data[:limit]:
                try:
                    match_data = self._convert_to_match_data(match)
                    if match_data:
                        match_data_list.append(match_data)
                except Exception as e:
                    logger.error(f"转换备用数据时出错: {e}")
                    continue
            
            logger.info(f"成功转换 {len(match_data_list)} 场比赛数据")
            return match_data_list
            
        except Exception as e:
            logger.error(f"加载备用数据时出错: {e}")
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
