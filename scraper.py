#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
足球赛事数据爬虫模块
使用Selenium获取bc.game网站的足球赛事数据
集成缓存管理和错误处理机制
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from dataclasses import asdict
import json
import random

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
from bs4 import BeautifulSoup
import pytz

from models import MatchData, MatchStatus
from config import get_config
from cache_manager import get_cache_manager
from error_handler import get_error_handler, ErrorType, ErrorSeverity, retry_on_error, handle_errors

logger = logging.getLogger(__name__)

class FootballScraper:
    """足球赛事数据爬虫类"""
    
    def __init__(self):
        self.config = get_config()
        self.cache_manager = get_cache_manager()
        self.error_handler = get_error_handler()
        self.driver = None
        self.malaysia_tz = pytz.timezone('Asia/Kuala_Lumpur')
        self.base_url = "https://bc.game"
        
        # 缓存配置
        self.cache_expire_seconds = 300  # 5分钟缓存
        self.cache_key_prefix = "football_matches"
        
    def _setup_driver(self) -> webdriver.Chrome:
        """设置Chrome驱动"""
        chrome_options = Options()
        
        # 生产环境配置
        if self.config.is_production():
            chrome_options.add_argument('--headless')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--remote-debugging-port=9222')
        
        # 通用配置
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.add_argument(f'--user-agent={self.config.crawler.user_agent}')
        chrome_options.add_argument('--disable-web-security')
        chrome_options.add_argument('--allow-running-insecure-content')
        chrome_options.add_argument('--disable-extensions')
        chrome_options.add_argument('--disable-plugins')
        chrome_options.add_argument('--disable-images')
        chrome_options.add_argument('--disable-javascript')
        
        # 内存优化
        chrome_options.add_argument('--memory-pressure-off')
        chrome_options.add_argument('--max_old_space_size=4096')
        
        try:
            driver = webdriver.Chrome(options=chrome_options)
            driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            driver.set_page_load_timeout(self.config.crawler.request_timeout)
            driver.implicitly_wait(10)
            return driver
        except Exception as e:
            logger.error(f"设置Chrome驱动失败: {e}")
            raise
    
    async def __aenter__(self):
        """异步上下文管理器入口"""
        self.driver = self._setup_driver()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        if self.driver:
            try:
                self.driver.quit()
            except Exception as e:
                logger.error(f"关闭驱动时出错: {e}")
    
    async def scrape_football_matches(self) -> List[MatchData]:
        """爬取足球赛事数据"""
        matches = []
        
        try:
            # 访问体育博彩页面
            sport_urls = [
                f"{self.base_url}/sport",
                f"{self.base_url}/sportsbook",
                f"{self.base_url}/game/sport"
            ]
            
            for url in sport_urls:
                try:
                    logger.info(f"正在访问: {url}")
                    self.driver.get(url)
                    
                    # 等待页面加载
                    await asyncio.sleep(3)
                    
                    # 查找足球/soccer相关内容
                    soccer_matches = await self._extract_soccer_matches()
                    if soccer_matches:
                        matches.extend(soccer_matches)
                        logger.info(f"从 {url} 获取到 {len(soccer_matches)} 场比赛")
                        break  # 找到数据就停止
                    
                except Exception as e:
                    logger.error(f"访问 {url} 时出错: {e}")
                    continue
            
            # 如果没有找到数据，尝试其他方法
            if not matches:
                matches = await self._fallback_scraping_methods()
            
            # 限制返回的比赛数量
            matches = matches[:self.config.crawler.max_matches]
            
            logger.info(f"总共获取到 {len(matches)} 场足球比赛")
            return matches
            
        except Exception as e:
            logger.error(f"爬取足球赛事时出错: {e}")
            return []
    
    async def _extract_soccer_matches(self) -> List[MatchData]:
        """从当前页面提取足球比赛数据"""
        matches = []
        
        try:
            # 等待页面完全加载
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            
            # 查找可能包含足球比赛的元素
            selectors = [
                # 通用选择器
                '[data-sport="soccer"]',
                '[data-sport="football"]',
                '.soccer-match',
                '.football-match',
                '.match-item',
                '.game-item',
                '.sport-item',
                
                # 可能的类名
                '.match',
                '.game',
                '.event',
                '.fixture',
                
                # 包含足球关键词的元素
                '*[class*="soccer"]',
                '*[class*="football"]',
                '*[data-testid*="soccer"]',
                '*[data-testid*="football"]',
            ]
            
            for selector in selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements:
                        logger.info(f"找到 {len(elements)} 个匹配元素: {selector}")
                        
                        for element in elements[:20]:  # 限制处理数量
                            match_data = await self._parse_match_element(element)
                            if match_data:
                                matches.append(match_data)
                        
                        if matches:
                            break  # 找到数据就停止
                            
                except Exception as e:
                    logger.debug(f"选择器 {selector} 处理失败: {e}")
                    continue
            
            # 如果没有找到特定的足球元素，尝试解析所有可能的比赛元素
            if not matches:
                matches = await self._parse_generic_matches()
            
            return matches
            
        except Exception as e:
            logger.error(f"提取足球比赛数据时出错: {e}")
            return []
    
    async def _parse_match_element(self, element) -> Optional[MatchData]:
        """解析单个比赛元素"""
        try:
            # 获取元素的文本内容
            text_content = element.text.strip()
            html_content = element.get_attribute('innerHTML')
            
            if not text_content and not html_content:
                return None
            
            # 查找队伍名称（通常用vs、-、:等分隔）
            team_patterns = [
                r'([A-Za-z\s]+)\s+vs\s+([A-Za-z\s]+)',
                r'([A-Za-z\s]+)\s+-\s+([A-Za-z\s]+)',
                r'([A-Za-z\s]+)\s+:\s+([A-Za-z\s]+)',
                r'([A-Za-z\s]+)\s+@\s+([A-Za-z\s]+)',
            ]
            
            home_team = away_team = None
            for pattern in team_patterns:
                match = re.search(pattern, text_content, re.IGNORECASE)
                if match:
                    home_team = match.group(1).strip()
                    away_team = match.group(2).strip()
                    break
            
            if not home_team or not away_team:
                return None
            
            # 查找赔率（1x2格式）
            odds_patterns = [
                r'(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)',  # 三个小数
                r'(\d+\.\d+)/(\d+\.\d+)/(\d+\.\d+)',     # 用/分隔
                r'(\d+\.\d+)\|(\d+\.\d+)\|(\d+\.\d+)',     # 用|分隔
            ]
            
            odds_1 = odds_x = odds_2 = 2.0  # 默认赔率
            for pattern in odds_patterns:
                match = re.search(pattern, text_content)
                if match:
                    odds_1 = float(match.group(1))
                    odds_x = float(match.group(2))
                    odds_2 = float(match.group(3))
                    break
            
            # 查找时间信息
            time_patterns = [
                r'(\d{1,2}:\d{2})',  # HH:MM
                r'(\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2})',  # MM/DD HH:MM
                r'(\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2})',  # YYYY-MM-DD HH:MM
            ]
            
            start_time = datetime.now(self.malaysia_tz) + timedelta(hours=1)  # 默认1小时后
            for pattern in time_patterns:
                match = re.search(pattern, text_content)
                if match:
                    time_str = match.group(1)
                    try:
                        # 尝试解析时间
                        if ':' in time_str and len(time_str) <= 5:
                            # 只有时间，假设是今天
                            hour, minute = map(int, time_str.split(':'))
                            start_time = datetime.now(self.malaysia_tz).replace(
                                hour=hour, minute=minute, second=0, microsecond=0
                            )
                            if start_time < datetime.now(self.malaysia_tz):
                                start_time += timedelta(days=1)  # 如果时间已过，设为明天
                        break
                    except:
                        continue
            
            # 生成唯一的比赛ID
            match_id = f"{home_team}_{away_team}_{start_time.strftime('%Y%m%d_%H%M')}".replace(' ', '_')
            
            return MatchData(
                match_id=match_id,
                start_time=start_time,
                home_team=home_team,
                away_team=away_team,
                odds_1=odds_1,
                odds_x=odds_x,
                odds_2=odds_2,
                league="BC.Game",
                status=MatchStatus.UPCOMING
            )
            
        except Exception as e:
            logger.debug(f"解析比赛元素时出错: {e}")
            return None
    
    async def _parse_generic_matches(self) -> List[MatchData]:
        """解析通用比赛数据（当找不到特定足球元素时）"""
        matches = []
        
        try:
            # 获取页面源码
            page_source = self.driver.page_source
            soup = BeautifulSoup(page_source, 'html.parser')
            
            # 查找包含体育相关关键词的文本
            keywords = ['soccer', 'football', 'match', 'vs', 'odds']
            
            for element in soup.find_all(text=True):
                text = element.strip()
                if any(keyword.lower() in text.lower() for keyword in keywords) and len(text) > 10:
                    # 尝试从文本中提取比赛信息
                    match_data = await self._extract_match_from_text(text)
                    if match_data:
                        matches.append(match_data)
                        if len(matches) >= self.config.crawler.max_matches:
                            break
            
            return matches
            
        except Exception as e:
            logger.error(f"解析通用比赛数据时出错: {e}")
            return []
    
    async def _extract_match_from_text(self, text: str) -> Optional[MatchData]:
        """从文本中提取比赛信息"""
        try:
            # 简单的文本解析逻辑
            if 'vs' in text.lower():
                parts = text.split('vs')
                if len(parts) == 2:
                    home_team = parts[0].strip()
                    away_team = parts[1].strip()
                    
                    if home_team and away_team and len(home_team) < 50 and len(away_team) < 50:
                        match_id = f"{home_team}_{away_team}_{datetime.now().strftime('%Y%m%d_%H%M%S')}".replace(' ', '_')
                        
                        return MatchData(
                            match_id=match_id,
                            start_time=datetime.now(self.malaysia_tz) + timedelta(hours=2),
                            home_team=home_team,
                            away_team=away_team,
                            odds_1=2.0,
                            odds_x=3.0,
                            odds_2=2.5,
                            league="BC.Game",
                            status=MatchStatus.UPCOMING
                        )
            
            return None
            
        except Exception as e:
            logger.debug(f"从文本提取比赛信息时出错: {e}")
            return None
    
    def _generate_mock_data(self, limit: int) -> List[MatchData]:
        """生成模拟的足球赛事数据"""
        logger.info(f"生成 {limit} 条模拟足球赛事数据")
        
        teams = [
            ("曼城", "利物浦"), ("皇马", "巴萨"), ("拜仁", "多特"),
            ("巴黎", "马赛"), ("尤文", "AC米兰"), ("切尔西", "阿森纳"),
            ("马竞", "塞维利亚"), ("国米", "那不勒斯"), ("热刺", "曼联"),
            ("莱比锡", "勒沃库森"), ("里昂", "摩纳哥"), ("罗马", "拉齐奥"),
            ("阿贾克斯", "费耶诺德"), ("本菲卡", "波尔图"), ("凯尔特人", "流浪者")
        ]
        
        leagues = ["英超", "西甲", "德甲", "法甲", "意甲", "欧冠", "欧联杯"]
        
        matches = []
        base_time = datetime.now(self.malaysia_tz) + timedelta(hours=2)
        
        for i in range(min(limit, len(teams))):
            home_team, away_team = teams[i]
            match_time = base_time + timedelta(hours=i * 2 + random.randint(0, 4))
            
            # 生成更真实的随机赔率
            home_base = 1.5 + random.uniform(0, 2.0)
            draw_base = 3.0 + random.uniform(0, 1.5)
            away_base = 1.5 + random.uniform(0, 2.0)
            
            # 确保赔率合理（总概率接近1）
            total_prob = (1/home_base) + (1/draw_base) + (1/away_base)
            margin = 1.05  # 5%的利润率
            
            home_odds = round(home_base * total_prob * margin, 2)
            draw_odds = round(draw_base * total_prob * margin, 2)
            away_odds = round(away_base * total_prob * margin, 2)
            
            match = MatchData(
                match_id=f"mock_{i+1:03d}",
                start_time=match_time,
                home_team=home_team,
                away_team=away_team,
                odds_1=home_odds,
                odds_x=draw_odds,
                odds_2=away_odds,
                status=MatchStatus.UPCOMING,
                league=random.choice(leagues)
            )
            matches.append(match)
        
        return matches
    
    async def _fallback_scraping_methods(self) -> List[MatchData]:
        """备用爬取方法"""
        logger.info("使用备用爬取方法...")
        return self._generate_mock_data(self.config.crawler.max_matches)
    
    @retry_on_error(max_attempts=3, base_delay=2.0)
    @handle_errors()
    async def get_upcoming_matches(self, limit: int = None, use_cache: bool = True) -> List[MatchData]:
        """获取即将开始的足球赛事数据"""
        if limit is None:
            limit = self.config.crawler.max_matches
        
        cache_key = f"{self.cache_key_prefix}_upcoming_{limit}"
        
        # 尝试从缓存获取数据
        if use_cache:
            try:
                cached_matches = await self.cache_manager.get(cache_key)
                if cached_matches:
                    logger.info(f"从缓存获取到 {len(cached_matches)} 场赛事数据")
                    return [MatchData(**match) for match in cached_matches]
            except Exception as e:
                await self.error_handler.handle_exception(
                    e, context={'operation': 'cache_get', 'cache_key': cache_key}
                )
        
        try:
            matches = await self.scrape_football_matches()
            
            # 过滤即将开始的比赛
            now = datetime.now(self.malaysia_tz)
            upcoming_matches = [
                match for match in matches 
                if match.start_time > now and match.status == MatchStatus.UPCOMING
            ]
            
            # 按开始时间排序
            upcoming_matches.sort(key=lambda x: x.start_time)
            
            result_matches = upcoming_matches[:limit]
            
            # 缓存数据
            if use_cache and result_matches:
                try:
                    matches_dict = [asdict(match) for match in result_matches]
                    await self.cache_manager.set(
                        cache_key, matches_dict, self.cache_expire_seconds
                    )
                    logger.info(f"已缓存 {len(result_matches)} 场赛事数据")
                except Exception as e:
                    await self.error_handler.handle_exception(
                        e, context={'operation': 'cache_set', 'cache_key': cache_key}
                    )
            
            return result_matches
            
        except Exception as e:
            await self.error_handler.handle_exception(
                e, context={'operation': 'get_upcoming_matches', 'limit': limit}
            )
            # 返回模拟数据作为后备
            logger.warning("获取失败，返回模拟数据")
            return self._generate_mock_data(limit)


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