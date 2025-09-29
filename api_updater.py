#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API端点自动更新机制
用于动态检测和更新BC.Game的API端点
"""

import json
import time
import requests
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional
from loguru import logger
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

class APIEndpointUpdater:
    """API端点自动更新器"""
    
    def __init__(self, config_file: str = "api_config.json"):
        self.config_file = Path(config_file)
        self.base_url = "https://bc.game"
        self.sport_url = "https://bc.game/sport"
        self.update_interval = 3600  # 1小时检查一次
        self.last_check_time = None
        
    def load_current_config(self) -> Dict:
        """加载当前API配置"""
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
            "notes": "默认配置"
        }
    
    def save_config(self, config: Dict) -> bool:
        """保存API配置"""
        try:
            config["last_updated"] = datetime.now().isoformat()
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            logger.info(f"API配置已保存到 {self.config_file}")
            return True
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")
            return False
    
    def test_endpoint(self, endpoint: str) -> Dict:
        """测试API端点的可用性"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7',
            'Referer': 'https://bc.game/sport',
            'Origin': 'https://bc.game'
        }
        
        try:
            response = requests.get(endpoint, headers=headers, timeout=10)
            
            result = {
                'endpoint': endpoint,
                'status_code': response.status_code,
                'available': response.status_code == 200,
                'response_size': len(response.content),
                'content_type': response.headers.get('content-type', ''),
                'test_time': datetime.now().isoformat()
            }
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    result['data_structure'] = list(data.keys()) if isinstance(data, dict) else type(data).__name__
                    
                    # 检查是否包含足球数据
                    if isinstance(data, dict) and 'data' in data and 'items' in data['data']:
                        items = data['data']['items']
                        soccer_count = 0
                        for item in items:
                            if 'sportInfo' in item and 'soccer' in item['sportInfo'].lower():
                                soccer_count += 1
                        result['contains_soccer'] = soccer_count > 0
                        result['soccer_matches'] = soccer_count
                        result['total_items'] = len(items)
                    
                except json.JSONDecodeError:
                    result['data_structure'] = 'non-json'
                    result['contains_soccer'] = False
            
            return result
            
        except Exception as e:
            return {
                'endpoint': endpoint,
                'status_code': 0,
                'available': False,
                'error': str(e),
                'test_time': datetime.now().isoformat()
            }
    
    def discover_new_endpoints(self) -> List[str]:
        """使用浏览器自动化发现新的API端点"""
        logger.info("开始自动发现新的API端点...")
        
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
                            discovered_endpoints.add(url)
                            
                except Exception as e:
                    continue
            
            driver.quit()
            
        except Exception as e:
            logger.error(f"浏览器自动化发现失败: {e}")
        
        logger.info(f"发现了 {len(discovered_endpoints)} 个潜在的API端点")
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
            new_endpoints = self.discover_new_endpoints()
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
            new_endpoints = self.discover_new_endpoints()
            
            # 测试新发现的端点
            for endpoint in new_endpoints:
                if endpoint not in working_endpoints:
                    result = self.test_endpoint(endpoint)
                    if result['available'] and result.get('contains_soccer', False):
                        working_endpoints.append(endpoint)
                        logger.info(f"发现新的可用端点: {endpoint}")
            
            # 更新配置
            if working_endpoints != current_endpoints:
                config['endpoints'] = working_endpoints[:5]  # 保留前5个
                config['discovery_method'] = 'auto_update'
                config['notes'] = f"自动更新于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}，替换了 {len(failed_endpoints)} 个失效端点"
                self.save_config(config)
                logger.info("API端点配置已更新")
                return True
        
        logger.info("API端点检查完成，无需更新")
        return False
    
    def should_check_update(self) -> bool:
        """判断是否需要检查更新"""
        if self.last_check_time is None:
            return True
        
        time_since_last_check = datetime.now() - self.last_check_time
        return time_since_last_check.total_seconds() > self.update_interval
    
    def auto_update_loop(self):
        """自动更新循环（可在后台运行）"""
        logger.info("启动API端点自动更新服务...")
        
        while True:
            try:
                if self.should_check_update():
                    self.check_and_update_endpoints()
                    self.last_check_time = datetime.now()
                
                # 等待下次检查
                time.sleep(300)  # 每5分钟检查一次是否需要更新
                
            except KeyboardInterrupt:
                logger.info("收到停止信号，退出自动更新服务")
                break
            except Exception as e:
                logger.error(f"自动更新服务出错: {e}")
                time.sleep(60)  # 出错后等待1分钟再继续

def main():
    """主函数 - 用于测试和手动更新"""
    updater = APIEndpointUpdater()
    
    print("🔄 API端点自动更新器")
    print("=" * 50)
    
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

if __name__ == "__main__":
    main()