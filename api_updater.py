#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
高级API端点更新服务
实现side探针 + 条件GET + 严格校验 + 断路器 + 原子写回
确保流量低、风控友好的前提下，持续稳定地抓取最新、有效的足球1X2数据端点
"""

import json
import time
import hashlib
import tempfile
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass, asdict
from enum import Enum
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from loguru import logger
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException


# 数据类和枚举定义
class CircuitState(Enum):
    """断路器状态"""
    CLOSED = "closed"      # 正常状态
    OPEN = "open"          # 熔断状态
    HALF_OPEN = "half_open" # 半开状态


class ValidationResult(Enum):
    """数据校验结果"""
    VALID = "valid"
    INVALID_DATE = "invalid_date"
    INVALID_ODDS = "invalid_odds"
    INVALID_SPORT = "invalid_sport"
    INCOMPLETE_DATA = "incomplete_data"


@dataclass
class ProbeResult:
    """探针检查结果"""
    endpoint: str
    has_changes: bool
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    content_hash: Optional[str] = None
    status_code: int = 0
    response_time: float = 0.0
    error: Optional[str] = None


@dataclass
class CircuitBreakerStats:
    """断路器统计信息"""
    state: CircuitState
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: Optional[datetime] = None
    last_success_time: Optional[datetime] = None
    total_requests: int = 0


@dataclass
class MatchData:
    """足球比赛数据"""
    home_team: str
    away_team: str
    match_time: datetime
    league: str
    odds_1: float  # 主胜
    odds_x: float  # 平局
    odds_2: float  # 客胜
    sport_type: str = "soccer"

    def is_valid(self) -> Tuple[bool, ValidationResult]:
        """验证数据有效性"""
        # 检查日期是否为未来
        if self.match_time <= datetime.now():
            return False, ValidationResult.INVALID_DATE
        
        # 检查赔率是否完整且有效
        if not all([self.odds_1 > 0, self.odds_x > 0, self.odds_2 > 0]):
            return False, ValidationResult.INVALID_ODDS
        
        # 检查是否为足球赛事
        if self.sport_type.lower() != "soccer":
            return False, ValidationResult.INVALID_SPORT
        
        # 检查数据完整性
        if not all([self.home_team, self.away_team, self.league]):
            return False, ValidationResult.INCOMPLETE_DATA
        
        return True, ValidationResult.VALID


class RateLimiter:
    """请求频率限制器"""
    
    def __init__(self, max_requests: int = 10, time_window: int = 60):
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests = []
        self._lock = threading.Lock()
    
    def can_proceed(self) -> bool:
        """检查是否可以发起请求"""
        with self._lock:
            now = time.time()
            # 清理过期的请求记录
            self.requests = [req_time for req_time in self.requests 
                           if now - req_time < self.time_window]
            
            if len(self.requests) < self.max_requests:
                self.requests.append(now)
                return True
            return False
    
    def wait_time(self) -> float:
        """获取需要等待的时间"""
        with self._lock:
            if not self.requests:
                return 0.0
            oldest_request = min(self.requests)
            return max(0.0, self.time_window - (time.time() - oldest_request))


class CircuitBreaker:
    """断路器实现"""
    
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60, 
                 success_threshold: int = 3):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self.stats = CircuitBreakerStats(state=CircuitState.CLOSED)
        self._lock = threading.Lock()
    
    def can_execute(self) -> bool:
        """检查是否可以执行请求"""
        with self._lock:
            if self.stats.state == CircuitState.CLOSED:
                return True
            elif self.stats.state == CircuitState.OPEN:
                # 检查是否可以转为半开状态
                if (self.stats.last_failure_time and 
                    datetime.now() - self.stats.last_failure_time > 
                    timedelta(seconds=self.recovery_timeout)):
                    self.stats.state = CircuitState.HALF_OPEN
                    self.stats.success_count = 0
                    return True
                return False
            else:  # HALF_OPEN
                return True
    
    def record_success(self):
        """记录成功"""
        with self._lock:
            self.stats.success_count += 1
            self.stats.total_requests += 1
            self.stats.last_success_time = datetime.now()
            
            if self.stats.state == CircuitState.HALF_OPEN:
                if self.stats.success_count >= self.success_threshold:
                    self.stats.state = CircuitState.CLOSED
                    self.stats.failure_count = 0
    
    def record_failure(self):
        """记录失败"""
        with self._lock:
            self.stats.failure_count += 1
            self.stats.total_requests += 1
            self.stats.last_failure_time = datetime.now()
            
            if (self.stats.state in [CircuitState.CLOSED, CircuitState.HALF_OPEN] and
                self.stats.failure_count >= self.failure_threshold):
                self.stats.state = CircuitState.OPEN


class AdvancedAPIEndpointUpdater:
    """高级API端点更新服务"""
    
    def __init__(self, config_file: str = "api_config.json"):
        self.config_file = Path(config_file)
        self.cache_file = Path(config_file.replace('.json', '_cache.json'))
        self.base_url = "https://bc.game"
        self.sport_url = "https://bc.game/sport"
        self.update_interval = 3600  # 1小时检查一次
        self.probe_interval = 300   # 5分钟探针检查一次
        self.last_check_time = None
        self.last_probe_time = None
        
        # 高级功能组件
        self.circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=300)
        self.rate_limiter = RateLimiter(max_requests=20, time_window=60)
        self.session = requests.Session()
        self._setup_session()
        
        # 缓存数据
        self.endpoint_cache = {}
        self.content_hashes = {}
        
        # 线程安全锁
        self._config_lock = threading.Lock()
        
        logger.info("高级API端点更新服务初始化完成")
    
    def _setup_session(self):
        """配置HTTP会话"""
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Referer': 'https://bc.game/sport',
            'Origin': 'https://bc.game',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache'
        })
        
        # 设置连接池和超时
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10,
            pool_maxsize=20,
            max_retries=3
        )
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
    
    def _load_cache(self) -> Dict:
        """加载缓存数据"""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    cache_data = json.load(f)
                    self.endpoint_cache = cache_data.get('endpoint_cache', {})
                    self.content_hashes = cache_data.get('content_hashes', {})
                    return cache_data
            except Exception as e:
                logger.error(f"加载缓存文件失败: {e}")
        return {}
    
    def _save_cache(self):
        """保存缓存数据"""
        try:
            cache_data = {
                'endpoint_cache': self.endpoint_cache,
                'content_hashes': self.content_hashes,
                'last_updated': datetime.now().isoformat()
            }
            self._atomic_write(self.cache_file, json.dumps(cache_data, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.error(f"保存缓存文件失败: {e}")
        
    def load_current_config(self) -> Dict:
        """加载当前API配置"""
        with self._config_lock:
            if self.config_file.exists():
                try:
                    with open(self.config_file, 'r', encoding='utf-8') as f:
                        return json.load(f)
                except Exception as e:
                    logger.error(f"加载配置文件失败: {e}")
            
            # 返回默认配置
            return {
                "endpoints": [],
                "last_updated": None,
                "discovery_method": "manual",
                "notes": "默认配置",
                "version": "2.0",
                "features": ["side_probe", "conditional_get", "strict_validation", "circuit_breaker", "atomic_writeback"]
            }
    
    @contextmanager
    def _atomic_write(self, file_path: Path, content: str):
        """原子写回机制 - 使用临时文件+原子重命名"""
        temp_file = None
        try:
            # 创建临时文件
            with tempfile.NamedTemporaryFile(
                mode='w', 
                encoding='utf-8', 
                suffix='.tmp',
                dir=file_path.parent,
                delete=False
            ) as temp_file:
                temp_file.write(content)
                temp_file.flush()
                temp_path = Path(temp_file.name)
            
            # 原子重命名
            temp_path.replace(file_path)
            logger.debug(f"原子写回完成: {file_path}")
            yield
            
        except Exception as e:
            # 清理临时文件
            if temp_file and Path(temp_file.name).exists():
                try:
                    Path(temp_file.name).unlink()
                except:
                    pass
            raise e
    
    def save_config(self, config: Dict) -> bool:
        """保存API配置（原子写回）"""
        with self._config_lock:
            try:
                config["last_updated"] = datetime.now().isoformat()
                config["version"] = "2.0"
                content = json.dumps(config, ensure_ascii=False, indent=2)
                
                with self._atomic_write(self.config_file, content):
                    pass
                
                logger.info(f"API配置已原子保存到 {self.config_file}")
                return True
            except Exception as e:
                logger.error(f"保存配置文件失败: {e}")
                return False
    
    def _side_probe(self, endpoint: str) -> ProbeResult:
        """Side探针 - 轻量级检查API版本/接口变化"""
        try:
            # 发送HEAD请求进行轻量级探测
            response = self.session.head(endpoint, timeout=5)
            
            etag = response.headers.get('ETag')
            last_modified = response.headers.get('Last-Modified')
            content_length = response.headers.get('Content-Length')
            
            # 计算响应头哈希作为版本标识
            version_data = f"{etag}:{last_modified}:{content_length}:{response.status_code}"
            content_hash = hashlib.md5(version_data.encode()).hexdigest()
            
            cached_hash = self.content_hashes.get(endpoint)
            has_changed = cached_hash != content_hash
            
            probe_result = ProbeResult(
                endpoint=endpoint,
                has_changes=has_changed,
                etag=etag,
                last_modified=last_modified,
                content_hash=content_hash,
                status_code=response.status_code,
                response_time=response.elapsed.total_seconds()
            )
            
            # 更新缓存
            if endpoint not in self.endpoint_cache:
                self.endpoint_cache[endpoint] = {}
            self.endpoint_cache[endpoint]['content_hash'] = content_hash
            self.endpoint_cache[endpoint]['last_probe'] = datetime.now().isoformat()
            
            logger.info(f"Side探针完成: {endpoint} - 变化: {has_changed}")
            return probe_result
            
        except Exception as e:
            logger.error(f"Side探针失败 {endpoint}: {e}")
            return ProbeResult(
                endpoint=endpoint,
                has_changes=True,  # 探针失败时假设有变化
                content_hash="",
                etag=None,
                last_modified=None,
                status_code=0,
                response_time=0.0,
                error=str(e)
            )
    
    def _conditional_get(self, endpoint: str, probe_result: ProbeResult = None) -> requests.Response:
        """条件GET请求 - 使用If-None-Match和If-Modified-Since头"""
        headers = {}
        
        # 使用探针结果或缓存信息设置条件头
        if probe_result:
            if probe_result.etag:
                headers['If-None-Match'] = probe_result.etag
            if probe_result.last_modified:
                headers['If-Modified-Since'] = probe_result.last_modified
        else:
            # 从缓存获取条件头信息
            cached_info = self.endpoint_cache.get(endpoint, {})
            if cached_info.get('etag'):
                headers['If-None-Match'] = cached_info['etag']
            if cached_info.get('last_modified'):
                headers['If-Modified-Since'] = cached_info['last_modified']
        
        # 发送条件GET请求
        response = self.session.get(endpoint, headers=headers, timeout=15)
        
        # 更新缓存信息
        if response.status_code == 200:
            if endpoint not in self.endpoint_cache:
                self.endpoint_cache[endpoint] = {}
            self.endpoint_cache[endpoint]['etag'] = response.headers.get('ETag')
            self.endpoint_cache[endpoint]['last_modified'] = response.headers.get('Last-Modified')
            self.endpoint_cache[endpoint]['last_fetch'] = datetime.now().isoformat()
        
        logger.info(f"条件GET请求: {endpoint} - 状态码: {response.status_code}")
        return response
    
    def test_endpoint(self, endpoint: str) -> Dict:
        """测试API端点的可用性（集成探针和条件GET）"""
        # 检查断路器状态
        if not self.circuit_breaker.can_execute():
            logger.warning(f"断路器开启，跳过端点测试: {endpoint}")
            return {
                'endpoint': endpoint,
                'status_code': 0,
                'available': False,
                'error': 'Circuit breaker is open',
                'test_time': datetime.now().isoformat()
            }
        
        # 检查频率限制
        if not self.rate_limiter.can_proceed():
            wait_time = self.rate_limiter.wait_time()
            logger.warning(f"频率限制，需等待 {wait_time:.1f}s: {endpoint}")
            return {
                'endpoint': endpoint,
                'status_code': 0,
                'available': False,
                'error': f'Rate limited, wait {wait_time:.1f}s',
                'test_time': datetime.now().isoformat()
            }
        
        try:
            # 首先进行side探针检查
            probe_result = self._side_probe(endpoint)
            
            # 如果没有变化且有缓存，直接返回缓存结果
            if not probe_result.has_changes and endpoint in self.endpoint_cache:
                cached_result = self.endpoint_cache[endpoint].get('test_result')
                if cached_result:
                    logger.info(f"使用缓存结果: {endpoint}")
                    cached_result['from_cache'] = True
                    return cached_result
            
            # 使用条件GET请求获取数据
            response = self._conditional_get(endpoint, probe_result)
            
            result = {
                'endpoint': endpoint,
                'status_code': response.status_code,
                'available': response.status_code in [200, 304],
                'response_size': len(response.content) if response.status_code == 200 else 0,
                'content_type': response.headers.get('content-type', ''),
                'test_time': datetime.now().isoformat(),
                'response_time': response.elapsed.total_seconds(),
                'from_cache': False,
                'probe_result': {
                    'has_changes': probe_result.has_changes,
                    'content_hash': probe_result.content_hash
                }
            }
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    result['data_structure'] = list(data.keys()) if isinstance(data, dict) else type(data).__name__
                    
                    # 严格校验 - 使用专门的校验方法
                    validation_result = self._strict_validate_match_data(data)
                    
                    result['contains_soccer'] = validation_result['valid_matches'] > 0
                    result['soccer_matches'] = validation_result['valid_matches']
                    result['total_items'] = validation_result['total_matches']
                    result['validation_passed'] = validation_result['is_valid']
                    result['validation_errors'] = validation_result['errors']
                    result['validation_warnings'] = validation_result['warnings']
                    
                    # 记录详细的校验信息
                    if validation_result['errors']:
                        logger.warning(f"端点 {endpoint} 校验错误: {validation_result['errors']}")
                    if validation_result['warnings']:
                        logger.info(f"端点 {endpoint} 校验警告: {validation_result['warnings'][:3]}...")  # 只显示前3个警告
                    
                    # 计算内容哈希
                    content_hash = hashlib.md5(response.content).hexdigest()
                    result['content_hash'] = content_hash
                    self.content_hashes[endpoint] = content_hash
                    
                except json.JSONDecodeError:
                    result['data_structure'] = 'non-json'
                    result['contains_soccer'] = False
                    result['validation_passed'] = False
            elif response.status_code == 304:
                result['data_structure'] = 'not_modified'
                result['contains_soccer'] = True  # 假设之前验证过
                result['validation_passed'] = True
            
            # 缓存测试结果
            if endpoint not in self.endpoint_cache:
                self.endpoint_cache[endpoint] = {}
            self.endpoint_cache[endpoint]['test_result'] = result
            
            # 记录断路器成功
            if result['available']:
                self.circuit_breaker.record_success()
            else:
                self.circuit_breaker.record_failure()
            
            logger.info(f"端点测试完成: {endpoint} - 状态码: {response.status_code}")
            return result
            
        except Exception as e:
            # 记录断路器失败
            self.circuit_breaker.record_failure()
            
            logger.error(f"测试端点失败 {endpoint}: {e}")
            return {
                'endpoint': endpoint,
                'status_code': 0,
                'available': False,
                'error': str(e),
                'test_time': datetime.now().isoformat(),
                'validation_passed': False
            }
    
    def _strict_validate_match_data(self, data) -> Dict:
        """严格校验足球比赛数据"""
        errors = []
        warnings = []
        valid_matches = 0
        total_matches = 0
        
        try:
            # 处理不同的数据格式
            items = []
            if isinstance(data, dict):
                if 'data' in data:
                    data_section = data.get('data', {})
                    if isinstance(data_section, dict):
                        items = data_section.get('items', [])
                    elif isinstance(data_section, list):
                        items = data_section
                    else:
                        items = [data_section] if data_section else []
                elif 'items' in data:
                    items = data.get('items', [])
                else:
                    # 可能整个dict就是一个item
                    items = [data]
            elif isinstance(data, list):
                items = data
            else:
                errors.append(f"不支持的数据格式：{type(data)}")
                return {'is_valid': False, 'errors': errors, 'warnings': warnings, 'valid_matches': 0, 'total_matches': 0}
            
            if not items:
                errors.append("数据为空：没有比赛项目")
                return {'is_valid': False, 'errors': errors, 'warnings': warnings, 'valid_matches': 0, 'total_matches': 0}
            
            current_time = datetime.now()
            
            for item in items:
                total_matches += 1
                
                # 确保item是字典类型
                if not isinstance(item, dict):
                    warnings.append(f"跳过非字典类型数据：{type(item)}")
                    continue
                
                # 1. 检查赛事类型 - 只处理soccer
                sport_info = item.get('sportInfo', '').lower()
                if 'soccer' not in sport_info and 'football' not in sport_info:
                    continue  # 跳过非足球赛事
                
                # 2. 检查日期 - 不要历史日期
                start_time = item.get('startTime', 0)
                if start_time:
                    try:
                        match_time = datetime.fromtimestamp(start_time / 1000)
                        if match_time < current_time:
                            warnings.append(f"发现历史比赛：{item.get('homeTeam', '')} vs {item.get('awayTeam', '')}")
                            continue  # 跳过历史比赛
                    except:
                        errors.append(f"无效的比赛时间：{start_time}")
                        continue
                
                # 3. 检查1X2赔率完整性
                odds = item.get('odds', {})
                if not isinstance(odds, dict):
                    warnings.append(f"赔率数据格式错误：{type(odds)}")
                    continue
                    
                odds_1 = odds.get('1') or odds.get('home')
                odds_x = odds.get('X') or odds.get('draw')
                odds_2 = odds.get('2') or odds.get('away')
                
                if not all([odds_1, odds_x, odds_2]):
                    warnings.append(f"赔率不完整：{item.get('homeTeam', '')} vs {item.get('awayTeam', '')}")
                    continue
                
                try:
                    odds_1_val = float(odds_1)
                    odds_x_val = float(odds_x)
                    odds_2_val = float(odds_2)
                    
                    # 检查赔率合理性
                    if not all([odds_1_val > 1.0, odds_x_val > 1.0, odds_2_val > 1.0]):
                        warnings.append(f"赔率异常：{odds_1_val}, {odds_x_val}, {odds_2_val}")
                        continue
                        
                except (ValueError, TypeError):
                    errors.append(f"赔率格式错误：{odds_1}, {odds_x}, {odds_2}")
                    continue
                
                # 4. 检查基本信息完整性
                if not all([item.get('homeTeam'), item.get('awayTeam')]):
                    warnings.append("缺少队伍信息")
                    continue
                
                valid_matches += 1
            
            # 计算验证结果
            is_valid = valid_matches > 0 and len(errors) == 0
            
            if valid_matches == 0:
                errors.append("没有找到有效的足球比赛数据")
            
            return {
                'is_valid': is_valid,
                'errors': errors,
                'warnings': warnings,
                'valid_matches': valid_matches,
                'total_matches': total_matches
            }
            
        except Exception as e:
            import traceback
            error_details = f"数据校验异常：{str(e)} - 行号：{traceback.format_exc()}"
            errors.append(error_details)
            logger.error(f"校验异常详情：{error_details}")
            return {
                'is_valid': False,
                'errors': errors,
                'warnings': warnings,
                'valid_matches': 0,
                'total_matches': total_matches
            }
    
    async def discover_new_endpoints(self) -> List[str]:
        """使用Playwright优化版本发现新的API端点"""
        logger.info("开始使用Playwright自动发现新的API端点...")
        
        try:
            # 导入优化后的API发现器
            from api_discovery import BCGameAPIDiscovery
            
            # 创建API发现器实例
            discovery = BCGameAPIDiscovery(
                headless=True,
                browser_type='chromium',
                max_concurrent=2
            )
            
            # 发现API端点
            discovered_apis = await discovery.discover_apis([self.sport_url])
            
            # 关闭浏览器
            await discovery.close()
            
            # 提取有效的API端点
            valid_endpoints = []
            for api_info in discovered_apis:
                endpoint = api_info.get('url')
                if endpoint and self._is_potential_api_url(endpoint):
                    # 使用同步方法测试端点
                    test_result = self.test_endpoint(endpoint)
                    if test_result.get('validation_passed', False):
                        valid_endpoints.append(endpoint)
                        logger.info(f"发现并验证新端点: {endpoint}")
                    else:
                        logger.warning(f"端点验证失败: {endpoint}")
            
            logger.info(f"Playwright发现了 {len(valid_endpoints)} 个有效的API端点")
            return valid_endpoints
            
        except ImportError as e:
            logger.error(f"无法导入api_discovery模块: {e}")
            return self._fallback_discover_endpoints()
        except Exception as e:
            logger.error(f"Playwright API发现失败: {e}")
            return self._fallback_discover_endpoints()
    
    def _fallback_discover_endpoints(self) -> List[str]:
        """回退到Selenium的发现方法"""
        logger.info("回退到Selenium发现方法...")
        
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.common.by import By
        except ImportError:
            logger.error("Selenium未安装，无法进行API发现")
            return []
        
        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.add_experimental_option('excludeSwitches', ['enable-automation'])
        
        # 启用网络日志
        chrome_options.add_experimental_option('perfLoggingPrefs', {
            'enableNetwork': True,
            'enablePage': False,
        })
        chrome_options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
        
        discovered_endpoints = set()
        
        try:
            driver = webdriver.Chrome(options=chrome_options)
            
            # 访问体育页面
            logger.info(f"访问 {self.sport_url}")
            driver.get(self.sport_url)
            
            # 等待页面加载
            time.sleep(5)
            
            # 尝试点击足球相关链接
            try:
                # 查找并点击足球链接
                soccer_links = driver.find_elements(By.XPATH, "//a[contains(@href, 'soccer') or contains(text(), 'Soccer') or contains(text(), 'Football')]")
                for link in soccer_links[:3]:  # 只点击前3个链接
                    try:
                        driver.execute_script("arguments[0].click();", link)
                        time.sleep(2)
                    except:
                        continue
            except:
                pass
            
            # 获取网络日志
            logs = driver.get_log('performance')
            
            for log in logs:
                try:
                    message = json.loads(log['message'])
                    if message['message']['method'] == 'Network.responseReceived':
                        url = message['message']['params']['response']['url']
                        
                        # 检查是否是潜在的API URL
                        if self._is_potential_api_url(url):
                            # 测试端点并进行严格校验
                            test_result = self.test_endpoint(url)
                            if test_result.get('validation_passed', False):
                                discovered_endpoints.add(url)
                                logger.info(f"发现并验证新端点: {url}")
                            else:
                                logger.warning(f"端点验证失败: {url}")
                            
                except Exception as e:
                    continue
            
            driver.quit()
            
        except Exception as e:
            logger.error(f"Selenium浏览器自动化发现失败: {e}")
        
        logger.info(f"Selenium发现了 {len(discovered_endpoints)} 个潜在的API端点")
        return list(discovered_endpoints)
    
    def _is_potential_api_url(self, url: str) -> bool:
        """判断URL是否是潜在的API端点"""
        api_indicators = [
            '/api/',
            '/cache/',
            'platform-sports',
            'live10',
            'prematch',
            'live',
            '.json'
        ]
        
        # 必须是bc.game域名
        if 'bc.game' not in url:
            return False
        
        # 检查是否包含API指示符
        return any(indicator in url.lower() for indicator in api_indicators)
    
    def check_and_update_endpoints(self) -> bool:
        """检查并更新API端点"""
        logger.info("开始检查API端点状态...")
        
        # 加载当前配置
        config = self.load_current_config()
        current_endpoints = config.get('endpoints', [])
        
        if not current_endpoints:
            logger.warning("没有配置的API端点，开始自动发现...")
            try:
                import asyncio
                new_endpoints = asyncio.run(self.discover_new_endpoints())
            except Exception as e:
                logger.error(f"异步API发现失败，使用回退方法: {e}")
                new_endpoints = self._fallback_discover_endpoints()
            
            if new_endpoints:
                config['endpoints'] = new_endpoints[:3]  # 保留前3个
                config['discovery_method'] = 'auto_discovery'
                config['notes'] = f"自动发现于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                self.save_config(config)
                return True
            return False
        
        # 测试现有端点
        working_endpoints = []
        failed_endpoints = []
        
        for endpoint in current_endpoints:
            result = self.test_endpoint(endpoint)
            if result['available']:
                working_endpoints.append(endpoint)
                logger.info(f"端点可用: {endpoint}")
            else:
                failed_endpoints.append(endpoint)
                logger.warning(f"端点失效: {endpoint} (状态码: {result.get('status_code', 'N/A')})")
        
        # 如果有端点失效，尝试发现新端点
        if failed_endpoints:
            logger.info(f"检测到 {len(failed_endpoints)} 个失效端点，开始发现新端点...")
            try:
                import asyncio
                new_endpoints = asyncio.run(self.discover_new_endpoints())
            except Exception as e:
                logger.error(f"异步API发现失败，使用回退方法: {e}")
                new_endpoints = self._fallback_discover_endpoints()
            
            # 测试新发现的端点
            for endpoint in new_endpoints:
                if endpoint not in working_endpoints:
                    result = self.test_endpoint(endpoint)
                    if result['available'] and result.get('contains_soccer', False):
                        working_endpoints.append(endpoint)
                        logger.info(f"发现新的可用端点: {endpoint}")
            
            # 更新配置（原子写回）
            if working_endpoints != current_endpoints:
                config['endpoints'] = working_endpoints[:5]  # 保留前5个
                config['discovery_method'] = 'auto_update'
                config['notes'] = f"自动更新于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}，替换了 {len(failed_endpoints)} 个失效端点"
                if self.save_config(config):
                    logger.info("API端点配置已原子更新")
                    return True
                else:
                    logger.error("API端点配置保存失败")
                    return False
        
        logger.info("API端点检查完成，无需更新")
        return False
    
    def should_check_update(self) -> bool:
        """判断是否需要检查更新"""
        if self.last_check_time is None:
            return True
        
        time_since_last_check = datetime.now() - self.last_check_time
        return time_since_last_check.total_seconds() > self.update_interval
    
    def _retry_with_backoff(self, func, max_retries: int = 3, base_delay: float = 1.0):
        """带指数退避的重试机制"""
        for attempt in range(max_retries):
            try:
                return func()
            except Exception as e:
                if attempt == max_retries - 1:
                    raise e
                
                delay = base_delay * (2 ** attempt)
                logger.warning(f"操作失败，{delay:.1f}秒后重试 (尝试 {attempt + 1}/{max_retries}): {e}")
                time.sleep(delay)
    
    def _should_probe(self, endpoint: str) -> bool:
        """判断是否需要进行探针检查"""
        if endpoint not in self.endpoint_cache:
            return True
        
        last_probe = self.endpoint_cache[endpoint].get('last_probe')
        if not last_probe:
            return True
        
        try:
            last_probe_time = datetime.fromisoformat(last_probe)
            time_since_probe = datetime.now() - last_probe_time
            return time_since_probe.total_seconds() > self.probe_interval
        except:
            return True
    
    def auto_update_loop(self, interval_minutes: int = 30):
        """增强的自动更新循环"""
        logger.info(f"🚀 启动高级自动更新循环，间隔: {interval_minutes} 分钟")
        logger.info(f"🔧 功能特性: Side探针 + 条件GET + 严格校验 + 断路器 + 原子写回")
        
        consecutive_failures = 0
        max_consecutive_failures = 5
        
        while True:
            try:
                start_time = datetime.now()
                logger.info(f"🔄 开始自动检查和更新API端点... ({start_time.strftime('%Y-%m-%d %H:%M:%S')})")
                
                # 使用重试机制执行更新
                updated = self._retry_with_backoff(
                    lambda: self.check_and_update_endpoints(),
                    max_retries=3,
                    base_delay=2.0
                )
                
                # 执行side探针检查
                config = self.load_current_config()
                probe_results = []
                
                for endpoint in config.get('endpoints', []):
                    if self._should_probe(endpoint):
                        try:
                            probe_result = self._side_probe(endpoint)
                            probe_results.append(probe_result)
                            
                            if probe_result.has_changes:
                                logger.info(f"🔍 探针检测到变化: {endpoint}")
                        except Exception as e:
                            logger.error(f"探针检查失败 {endpoint}: {e}")
                
                # 保存缓存和统计信息
                self._save_cache()
                
                # 记录统计信息
                end_time = datetime.now()
                duration = (end_time - start_time).total_seconds()
                
                cb_stats = self.circuit_breaker.stats
                logger.info(f"✅ 更新周期完成 (耗时: {duration:.1f}s)")
                logger.info(f"📊 断路器状态: {cb_stats.state.value} | 成功: {cb_stats.success_count} | 失败: {cb_stats.failure_count}")
                logger.info(f"🔍 探针检查: {len(probe_results)} 个端点")
                
                if updated:
                    logger.info("✅ API端点已更新")
                    consecutive_failures = 0
                else:
                    logger.info("ℹ️ API端点无需更新")
                    
            except KeyboardInterrupt:
                logger.info("收到停止信号，退出自动更新服务")
                break
            except Exception as e:
                consecutive_failures += 1
                logger.error(f"❌ 自动更新过程中发生错误 (连续失败: {consecutive_failures}): {e}")
                
                # 如果连续失败次数过多，增加等待时间
                if consecutive_failures >= max_consecutive_failures:
                    extended_wait = interval_minutes * 2
                    logger.warning(f"⚠️ 连续失败 {consecutive_failures} 次，延长等待时间至 {extended_wait} 分钟")
                    time.sleep(extended_wait * 60)
                    consecutive_failures = 0
                    continue
            
            # 等待下次更新
            next_check = datetime.now() + timedelta(minutes=interval_minutes)
            logger.info(f"⏰ 下次检查时间: {next_check.strftime('%Y-%m-%d %H:%M:%S')}")
            time.sleep(interval_minutes * 60)

def main():
    """主函数 - 用于测试和手动更新"""
    updater = AdvancedAPIEndpointUpdater()
    
    print("🔄 高级API端点自动更新器")
    print("=" * 50)
    
    # 加载缓存
    updater._load_cache()
    
    # 检查并更新端点
    updated = updater.check_and_update_endpoints()
    
    if updated:
        print("✅ API端点已更新")
    else:
        print("ℹ️ API端点无需更新")
    
    # 显示当前配置
    config = updater.load_current_config()
    print(f"\n📋 当前配置的API端点数量: {len(config.get('endpoints', []))}")
    for i, endpoint in enumerate(config.get('endpoints', []), 1):
        print(f"   {i}. {endpoint}")
    
    print(f"\n🕒 最后更新时间: {config.get('last_updated', 'N/A')}")
    print(f"📝 更新方式: {config.get('discovery_method', 'N/A')}")
    print(f"🔧 功能特性: {', '.join(config.get('features', []))}")
    
    # 显示断路器状态
    cb_stats = updater.circuit_breaker.stats
    print(f"\n⚡ 断路器状态: {cb_stats.state.value}")
    print(f"📊 请求统计: 成功 {cb_stats.success_count}, 失败 {cb_stats.failure_count}")
    
    # 保存缓存
    updater._save_cache()

# 为了向后兼容，创建别名
APIEndpointUpdater = AdvancedAPIEndpointUpdater

if __name__ == "__main__":
    main()
