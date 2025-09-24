#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
足球赛事爬虫机器人主程序
整合爬虫模块和Telegram机器人功能
"""

import asyncio
import logging
import signal
import sys
from typing import Optional

from telegram import Update
from config import get_config
from bot import FootballBot
from scraper import FootballScraper
from cache_manager import get_cache_manager
from error_handler import get_error_handler

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('football_bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

class FootballBotApp:
    """足球机器人应用主类"""
    
    def __init__(self):
        self.config = get_config()
        self.cache_manager = get_cache_manager()
        self.error_handler = get_error_handler()
        self.bot: Optional[FootballBot] = None
        self.scraper: Optional[FootballScraper] = None
        self.running = False
    
    async def initialize(self):
        """初始化应用组件"""
        try:
            logger.info("正在初始化足球机器人应用...")
            
            # 初始化缓存管理器
            await self.cache_manager.initialize()
            logger.info("缓存管理器初始化完成")
            
            # 初始化爬虫
            self.scraper = FootballScraper()
            logger.info("爬虫模块初始化完成")
            
            # 初始化机器人
            self.bot = FootballBot()
            await self.bot.initialize()
            logger.info("Telegram机器人初始化完成")
            
            logger.info("应用初始化完成")
            
        except Exception as e:
            logger.error(f"应用初始化失败: {e}")
            await self.error_handler.handle_exception(
                e, context={'operation': 'app_initialization'}
            )
            raise
    
    async def start(self):
        """启动应用"""
        try:
            if not self.bot:
                raise RuntimeError("机器人未初始化")
            
            logger.info("启动足球机器人应用...")
            self.running = True
            
            # 启动机器人
            await self.bot.application.initialize()
            await self.bot.application.start()
            
            if self.config.telegram.use_webhook:
                logger.info("使用Webhook模式启动机器人")
                # Webhook模式
                if hasattr(self.config.telegram, 'webhook_url') and self.config.telegram.webhook_url:
                    await self.bot.application.bot.set_webhook(
                        url=self.config.telegram.webhook_url,
                        allowed_updates=Update.ALL_TYPES
                    )
                    logger.info(f"Webhook设置完成: {self.config.telegram.webhook_url}")
            else:
                logger.info("使用轮询模式启动机器人")
                # 轮询模式 - 使用新的API，但这里只是启动，不阻塞
                # 实际的轮询将在run方法中处理
                logger.info("机器人已准备就绪，等待轮询启动")
            
        except Exception as e:
            logger.error(f"启动应用失败: {e}")
            await self.error_handler.handle_exception(
                e, context={'operation': 'app_start'}
            )
            raise
    
    async def stop(self):
        """停止应用"""
        try:
            logger.info("正在停止足球机器人应用...")
            self.running = False
            
            # 停止机器人
            if self.bot and self.bot.application:
                await self.bot.application.stop()
                await self.bot.application.shutdown()
                logger.info("机器人已停止")
            
            # 清理缓存
            if self.cache_manager:
                await self.cache_manager.cleanup()
                logger.info("缓存已清理")
            
            logger.info("应用已停止")
            
        except Exception as e:
            logger.error(f"停止应用时出错: {e}")
            await self.error_handler.handle_exception(
                e, context={'operation': 'app_stop'}
            )
    
    async def health_check(self) -> dict:
        """健康检查"""
        try:
            health_status = {
                'app_running': self.running,
                'timestamp': asyncio.get_event_loop().time(),
                'components': {}
            }
            
            # 检查机器人状态
            if self.bot:
                health_status['components']['bot'] = await self.bot.health_check()
            
            # 检查缓存状态
            if self.cache_manager:
                cache_stats = await self.cache_manager.get_stats()
                health_status['components']['cache'] = {
                    'status': 'healthy',
                    'stats': cache_stats
                }
            
            # 检查错误处理器状态
            if self.error_handler:
                error_stats = await self.error_handler.get_stats()
                health_status['components']['error_handler'] = {
                    'status': 'healthy',
                    'stats': error_stats
                }
            
            return health_status
            
        except Exception as e:
            logger.error(f"健康检查失败: {e}")
            return {
                'app_running': False,
                'error': str(e),
                'timestamp': asyncio.get_event_loop().time()
            }

# 全局应用实例
app: Optional[FootballBotApp] = None

async def signal_handler(signum, frame):
    """信号处理器"""
    logger.info(f"收到信号 {signum}，正在优雅关闭...")
    if app:
        await app.stop()
    sys.exit(0)

async def test_scraper():
    """测试爬虫功能"""
    try:
        logger.info("开始测试爬虫功能...")
        
        scraper = FootballScraper()
        matches = await scraper.get_upcoming_matches(limit=5)
        
        logger.info(f"获取到 {len(matches)} 场比赛:")
        for match in matches:
            logger.info(f"  {match.home_team} vs {match.away_team} - {match.start_time}")
            logger.info(f"  赔率: {match.odds_1} / {match.odds_x} / {match.odds_2}")
        
        return matches
        
    except Exception as e:
        logger.error(f"测试爬虫失败: {e}")
        return []

async def test_bot():
    """测试机器人功能"""
    try:
        logger.info("开始测试机器人功能...")
        
        bot = FootballBot()
        await bot.initialize()
        
        # 获取机器人信息
        bot_info = await bot.application.bot.get_me()
        logger.info(f"机器人信息: @{bot_info.username} ({bot_info.first_name})")
        
        return True
        
    except Exception as e:
        logger.error(f"测试机器人失败: {e}")
        return False

async def health_check_simple():
    """简化的健康检查，不需要Telegram token"""
    try:
        logger.info("开始健康检查...")
        
        health_status = {
            'timestamp': asyncio.get_event_loop().time(),
            'components': {}
        }
        
        # 检查缓存管理器
        try:
            cache_manager = get_cache_manager()
            await cache_manager.initialize()
            cache_stats = await cache_manager.get_stats()
            health_status['components']['cache'] = {
                'status': 'healthy',
                'stats': cache_stats
            }
            logger.info("缓存管理器: 健康")
        except Exception as e:
            health_status['components']['cache'] = {
                'status': 'error',
                'error': str(e)
            }
            logger.error(f"缓存管理器检查失败: {e}")
        
        # 检查错误处理器
        try:
            error_handler = get_error_handler()
            error_stats = await error_handler.get_stats()
            health_status['components']['error_handler'] = {
                'status': 'healthy',
                'stats': error_stats
            }
            logger.info("错误处理器: 健康")
        except Exception as e:
            health_status['components']['error_handler'] = {
                'status': 'error',
                'error': str(e)
            }
            logger.error(f"错误处理器检查失败: {e}")
        
        # 检查爬虫模块
        try:
            scraper = FootballScraper()
            health_status['components']['scraper'] = {
                'status': 'healthy',
                'message': '爬虫模块可用'
            }
            logger.info("爬虫模块: 健康")
        except Exception as e:
            health_status['components']['scraper'] = {
                'status': 'error',
                'error': str(e)
            }
            logger.error(f"爬虫模块检查失败: {e}")
        
        # 输出健康状态
        print(f"健康状态: {health_status}")
        logger.info("健康检查完成")
        
        return health_status
        
    except Exception as e:
        logger.error(f"健康检查失败: {e}")
        error_status = {
            'status': 'error',
            'error': str(e),
            'timestamp': asyncio.get_event_loop().time()
        }
        print(f"健康状态: {error_status}")
        return error_status

async def main():
    """主函数"""
    global app
    
    try:
        # 设置信号处理
        signal.signal(signal.SIGINT, lambda s, f: asyncio.create_task(signal_handler(s, f)))
        signal.signal(signal.SIGTERM, lambda s, f: asyncio.create_task(signal_handler(s, f)))
        
        # 检查命令行参数
        if len(sys.argv) > 1:
            command = sys.argv[1].lower()
            
            if command == 'test-scraper':
                await test_scraper()
                return
            elif command == 'test-bot':
                await test_bot()
                return
            elif command == 'health':
                await health_check_simple()
                return
            elif command in ['help', '-h', '--help']:
                print("使用方法:")
                print("  python main.py              # 启动机器人")
                print("  python main.py test-scraper # 测试爬虫功能")
                print("  python main.py test-bot     # 测试机器人功能")
                print("  python main.py health       # 检查应用健康状态")
                print("  python main.py help         # 显示帮助信息")
                return
        
        # 启动完整应用
        logger.info("启动足球赛事爬虫机器人...")
        
        # 直接使用机器人的run方法，避免复杂的应用层
        bot = FootballBot()
        await bot.run()
        
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在停止...")
    except Exception as e:
        logger.error(f"应用运行出错: {e}")
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("应用已停止")
    except Exception as e:
        logger.error(f"应用启动失败: {e}")
        sys.exit(1)
