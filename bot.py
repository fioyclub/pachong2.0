#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram机器人核心模块
实现足球赛事查询和投注建议功能
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import json
from dataclasses import asdict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    ContextTypes, MessageHandler, filters
)
from telegram.constants import ParseMode

from models import MatchData, UserSession, MatchStatus
from scraper import scrape_football_data
from cache_manager import CacheManager
from config import get_config

logger = logging.getLogger(__name__)

class FootballBot:
    """足球机器人类"""
    
    def __init__(self):
        self.config = get_config()
        self.cache_manager = CacheManager()
        self.user_sessions: Dict[int, UserSession] = {}
        self.application = None
        
    async def initialize(self):
        """初始化机器人"""
        try:
            # 尝试使用最简单的方式创建 Application，避免触发 Updater
            from telegram.ext import ApplicationBuilder
            
            # 创建 ApplicationBuilder 并禁用不必要的功能
            builder = ApplicationBuilder()
            builder.token(self.config.telegram.bot_token)
            
            # 尝试禁用可能触发 Updater 的功能
            try:
                # 在某些版本中，可以通过这种方式禁用 updater
                builder.updater(None)
            except:
                # 如果不支持，忽略这个设置
                pass
            
            self.application = builder.build()
            
            # 注册命令处理器
            self.application.add_handler(CommandHandler("start", self.start_command))
            self.application.add_handler(CommandHandler("check", self.check_command))
            self.application.add_handler(CommandHandler("compare", self.compare_command))
            self.application.add_handler(CommandHandler("bet", self.bet_command))
            self.application.add_handler(CommandHandler("help", self.help_command))
            self.application.add_handler(CommandHandler("status", self.status_command))
            
            # 注册回调查询处理器
            self.application.add_handler(CallbackQueryHandler(self.button_callback))
            
            # 注册消息处理器
            self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
            
            # 错误处理器
            self.application.add_error_handler(self.error_handler)
            
            logger.info("机器人初始化完成")
            
        except Exception as e:
            logger.error(f"机器人初始化失败: {e}")
            raise
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理/start命令"""
        user_id = update.effective_user.id
        username = update.effective_user.username or "用户"
        
        # 创建或更新用户会话
        self.user_sessions[user_id] = UserSession(
            user_id=user_id,
            username=username,
            last_activity=datetime.now(),
            preferences={"timezone": "Asia/Kuala_Lumpur", "language": "zh"}
        )
        
        welcome_text = f"""
🏈 **欢迎使用足球赛事机器人！** 🏈

你好 {username}！我可以帮你：

⚽ **查看即将开始的足球比赛**
📊 **比较不同比赛的赔率**
💡 **提供投注建议和分析**
📈 **实时更新比赛信息**

**可用命令：**
/check - 查看即将开始的比赛
/compare - 比较比赛赔率
/bet - 获取投注建议
/status - 查看系统状态
/help - 获取帮助信息

点击下方按钮开始使用！
        """
        
        # 创建内联键盘
        keyboard = [
            [InlineKeyboardButton("⚽ 查看比赛", callback_data="check_matches")],
            [InlineKeyboardButton("📊 比较赔率", callback_data="compare_odds")],
            [InlineKeyboardButton("💡 投注建议", callback_data="bet_advice")],
            [InlineKeyboardButton("❓ 帮助", callback_data="help")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            welcome_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
        
        logger.info(f"用户 {username} ({user_id}) 启动了机器人")
    
    async def check_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理/check命令 - 查看即将开始的比赛"""
        user_id = update.effective_user.id
        
        # 更新用户活动时间
        if user_id in self.user_sessions:
            self.user_sessions[user_id].last_activity = datetime.now()
        
        await update.message.reply_text("🔄 正在获取最新的足球比赛信息...")
        
        try:
            # 从缓存或重新获取比赛数据
            matches = await self._get_cached_matches()
            
            if not matches:
                await update.message.reply_text(
                    "😔 暂时没有找到即将开始的足球比赛。\n\n请稍后再试或联系管理员。"
                )
                return
            
            # 格式化比赛信息
            matches_text = "⚽ **即将开始的足球比赛** ⚽\n\n"
            
            for i, match in enumerate(matches[:10], 1):  # 限制显示10场比赛
                matches_text += f"{i}. {match.format_for_telegram()}\n\n"
            
            matches_text += f"\n📊 共找到 {len(matches)} 场比赛\n"
            matches_text += f"🕐 更新时间: {datetime.now().strftime('%H:%M:%S')}"
            
            # 创建操作按钮
            keyboard = [
                [InlineKeyboardButton("🔄 刷新数据", callback_data="refresh_matches")],
                [InlineKeyboardButton("📊 比较赔率", callback_data="compare_odds")],
                [InlineKeyboardButton("💡 投注建议", callback_data="bet_advice")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                matches_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup
            )
            
        except Exception as e:
            logger.error(f"处理check命令时出错: {e}")
            await update.message.reply_text(
                "❌ 获取比赛信息时出现错误，请稍后再试。"
            )
    
    async def compare_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理/compare命令 - 比较比赛赔率"""
        user_id = update.effective_user.id
        
        if user_id in self.user_sessions:
            self.user_sessions[user_id].last_activity = datetime.now()
        
        try:
            matches = await self._get_cached_matches()
            
            if not matches:
                await update.message.reply_text(
                    "😔 暂时没有比赛数据可供比较。\n\n请先使用 /check 命令获取比赛信息。"
                )
                return
            
            # 分析赔率
            analysis = self._analyze_odds(matches)
            
            compare_text = "📊 **赔率比较分析** 📊\n\n"
            
            # 最佳主胜赔率
            if analysis['best_home_win']:
                match = analysis['best_home_win']
                compare_text += f"🏆 **最佳主胜赔率**\n"
                compare_text += f"{match.home_team} vs {match.away_team}\n"
                compare_text += f"主胜赔率: {match.odds_1}\n\n"
            
            # 最佳平局赔率
            if analysis['best_draw']:
                match = analysis['best_draw']
                compare_text += f"⚖️ **最佳平局赔率**\n"
                compare_text += f"{match.home_team} vs {match.away_team}\n"
                compare_text += f"平局赔率: {match.odds_x}\n\n"
            
            # 最佳客胜赔率
            if analysis['best_away_win']:
                match = analysis['best_away_win']
                compare_text += f"🎯 **最佳客胜赔率**\n"
                compare_text += f"{match.home_team} vs {match.away_team}\n"
                compare_text += f"客胜赔率: {match.odds_2}\n\n"
            
            # 统计信息
            compare_text += f"📈 **统计信息**\n"
            compare_text += f"平均主胜赔率: {analysis['avg_odds_1']:.2f}\n"
            compare_text += f"平均平局赔率: {analysis['avg_odds_x']:.2f}\n"
            compare_text += f"平均客胜赔率: {analysis['avg_odds_2']:.2f}\n"
            
            keyboard = [
                [InlineKeyboardButton("💡 获取投注建议", callback_data="bet_advice")],
                [InlineKeyboardButton("⚽ 查看所有比赛", callback_data="check_matches")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                compare_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup
            )
            
        except Exception as e:
            logger.error(f"处理compare命令时出错: {e}")
            await update.message.reply_text(
                "❌ 比较赔率时出现错误，请稍后再试。"
            )
    
    async def bet_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理/bet命令 - 提供投注建议"""
        user_id = update.effective_user.id
        
        if user_id in self.user_sessions:
            self.user_sessions[user_id].last_activity = datetime.now()
        
        try:
            matches = await self._get_cached_matches()
            
            if not matches:
                await update.message.reply_text(
                    "😔 暂时没有比赛数据可供分析。\n\n请先使用 /check 命令获取比赛信息。"
                )
                return
            
            # 生成投注建议
            recommendations = self._generate_bet_recommendations(matches)
            
            bet_text = "💡 **智能投注建议** 💡\n\n"
            
            for i, rec in enumerate(recommendations[:5], 1):  # 显示前5个建议
                bet_text += f"**{i}. {rec['match'].home_team} vs {rec['match'].away_team}**\n"
                bet_text += f"🎯 建议: {rec['recommendation']}\n"
                bet_text += f"📊 赔率: {rec['odds']}\n"
                bet_text += f"⭐ 信心度: {rec['confidence']}\n"
                bet_text += f"💰 预期收益: {rec['expected_return']}\n"
                bet_text += f"📝 理由: {rec['reason']}\n\n"
            
            bet_text += "⚠️ **风险提示**\n"
            bet_text += "投注有风险，请理性投注，量力而行。\n"
            bet_text += "本建议仅供参考，不构成投资建议。\n\n"
            bet_text += f"🕐 分析时间: {datetime.now().strftime('%H:%M:%S')}"
            
            keyboard = [
                [InlineKeyboardButton("📊 查看赔率比较", callback_data="compare_odds")],
                [InlineKeyboardButton("⚽ 查看所有比赛", callback_data="check_matches")],
                [InlineKeyboardButton("🔄 刷新建议", callback_data="refresh_bet_advice")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                bet_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup
            )
            
        except Exception as e:
            logger.error(f"处理bet命令时出错: {e}")
            await update.message.reply_text(
                "❌ 生成投注建议时出现错误，请稍后再试。"
            )
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理/help命令"""
        help_text = """
❓ **帮助信息** ❓

**可用命令：**

⚽ `/start` - 启动机器人并查看欢迎信息
📊 `/check` - 查看即将开始的足球比赛
📈 `/compare` - 比较不同比赛的赔率
💡 `/bet` - 获取智能投注建议
📋 `/status` - 查看系统运行状态
❓ `/help` - 显示此帮助信息

**功能说明：**

🔍 **比赛查询** - 实时获取即将开始的足球比赛信息
📊 **赔率分析** - 智能分析和比较各场比赛的1x2赔率
💰 **投注建议** - 基于赔率分析提供投注参考
🔄 **自动更新** - 定期更新比赛和赔率信息

**使用技巧：**

• 使用内联按钮快速操作
• 定期刷新获取最新信息
• 关注系统状态确保数据准确性
• 理性投注，量力而行

**联系支持：**
如有问题或建议，请联系管理员。
        """
        
        await update.message.reply_text(
            help_text,
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理/status命令 - 显示系统状态"""
        try:
            # 获取系统状态信息
            cache_stats = await self.cache_manager.get_stats()
            active_users = len(self.user_sessions)
            
            status_text = "📋 **系统状态** 📋\n\n"
            status_text += f"🤖 机器人状态: ✅ 运行中\n"
            status_text += f"👥 活跃用户: {active_users}\n"
            status_text += f"💾 缓存状态: {cache_stats.get('status', '未知')}\n"
            status_text += f"📊 缓存条目: {cache_stats.get('entries', 0)}\n"
            status_text += f"🕐 运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            
            # 检查数据源状态
            try:
                test_matches = await self._get_cached_matches(force_refresh=False)
                if test_matches:
                    status_text += f"🌐 数据源状态: ✅ 正常 ({len(test_matches)} 场比赛)\n"
                else:
                    status_text += f"🌐 数据源状态: ⚠️ 无数据\n"
            except Exception as e:
                status_text += f"🌐 数据源状态: ❌ 异常\n"
            
            status_text += f"\n🔄 最后更新: {datetime.now().strftime('%H:%M:%S')}"
            
            keyboard = [
                [InlineKeyboardButton("🔄 刷新状态", callback_data="refresh_status")],
                [InlineKeyboardButton("⚽ 查看比赛", callback_data="check_matches")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                status_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup
            )
            
        except Exception as e:
            logger.error(f"处理status命令时出错: {e}")
            await update.message.reply_text(
                "❌ 获取系统状态时出现错误。"
            )
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理内联按钮回调"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        data = query.data
        
        # 更新用户活动时间
        if user_id in self.user_sessions:
            self.user_sessions[user_id].last_activity = datetime.now()
        
        try:
            if data == "check_matches":
                await self._handle_check_callback(query)
            elif data == "compare_odds":
                await self._handle_compare_callback(query)
            elif data == "bet_advice":
                await self._handle_bet_callback(query)
            elif data == "help":
                await self._handle_help_callback(query)
            elif data == "refresh_matches":
                await self._handle_refresh_matches_callback(query)
            elif data == "refresh_bet_advice":
                await self._handle_refresh_bet_callback(query)
            elif data == "refresh_status":
                await self._handle_refresh_status_callback(query)
            else:
                await query.edit_message_text("❌ 未知的操作")
                
        except Exception as e:
            logger.error(f"处理按钮回调时出错: {e}")
            await query.edit_message_text("❌ 处理请求时出现错误")
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理普通文本消息"""
        user_id = update.effective_user.id
        message_text = update.message.text
        
        # 更新用户活动时间
        if user_id in self.user_sessions:
            self.user_sessions[user_id].last_activity = datetime.now()
        
        # 简单的关键词响应
        if any(keyword in message_text.lower() for keyword in ['比赛', 'match', '足球', 'football']):
            await update.message.reply_text(
                "⚽ 你想查看足球比赛吗？使用 /check 命令查看即将开始的比赛！"
            )
        elif any(keyword in message_text.lower() for keyword in ['赔率', 'odds', '比较']):
            await update.message.reply_text(
                "📊 想比较赔率？使用 /compare 命令查看赔率分析！"
            )
        elif any(keyword in message_text.lower() for keyword in ['投注', 'bet', '建议']):
            await update.message.reply_text(
                "💡 需要投注建议？使用 /bet 命令获取智能分析！"
            )
        else:
            await update.message.reply_text(
                "🤖 我是足球赛事机器人！\n\n使用 /help 查看可用命令，或 /start 开始使用。"
            )
    
    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        """错误处理器"""
        logger.error(f"处理更新时出错: {context.error}")
        
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "❌ 处理请求时出现错误，请稍后再试。"
            )
    
    # 辅助方法
    async def _get_cached_matches(self, force_refresh: bool = False) -> List[MatchData]:
        """获取缓存的比赛数据"""
        cache_key = "football_matches"
        
        if not force_refresh:
            cached_data = await self.cache_manager.get(cache_key)
            if cached_data:
                return [MatchData(**match) for match in cached_data]
        
        # 获取新数据
        matches = await scrape_football_data()
        
        # 缓存数据
        if matches:
            await self.cache_manager.set(
                cache_key, 
                [asdict(match) for match in matches],
                expire_seconds=300  # 5分钟缓存
            )
        
        return matches
    
    def _analyze_odds(self, matches: List[MatchData]) -> Dict[str, Any]:
        """分析赔率数据"""
        if not matches:
            return {}
        
        # 找出最佳赔率
        best_home_win = max(matches, key=lambda x: x.odds_1)
        best_draw = max(matches, key=lambda x: x.odds_x)
        best_away_win = max(matches, key=lambda x: x.odds_2)
        
        # 计算平均赔率
        avg_odds_1 = sum(match.odds_1 for match in matches) / len(matches)
        avg_odds_x = sum(match.odds_x for match in matches) / len(matches)
        avg_odds_2 = sum(match.odds_2 for match in matches) / len(matches)
        
        return {
            'best_home_win': best_home_win,
            'best_draw': best_draw,
            'best_away_win': best_away_win,
            'avg_odds_1': avg_odds_1,
            'avg_odds_x': avg_odds_x,
            'avg_odds_2': avg_odds_2
        }
    
    def _generate_bet_recommendations(self, matches: List[MatchData]) -> List[Dict[str, Any]]:
        """生成投注建议"""
        recommendations = []
        
        for match in matches:
            # 简单的投注建议算法
            odds_sum = match.odds_1 + match.odds_x + match.odds_2
            
            # 计算隐含概率
            prob_1 = 1 / match.odds_1
            prob_x = 1 / match.odds_x
            prob_2 = 1 / match.odds_2
            
            # 找出最有价值的投注选项
            if match.odds_1 >= 2.0 and prob_1 > 0.4:
                recommendation = f"主胜 ({match.home_team})"
                odds = match.odds_1
                confidence = "⭐⭐⭐"
                expected_return = f"+{((match.odds_1 - 1) * 100):.1f}%"
                reason = "主队赔率合理，胜率较高"
            elif match.odds_2 >= 2.5 and prob_2 > 0.3:
                recommendation = f"客胜 ({match.away_team})"
                odds = match.odds_2
                confidence = "⭐⭐"
                expected_return = f"+{((match.odds_2 - 1) * 100):.1f}%"
                reason = "客队赔率较高，有价值"
            elif match.odds_x >= 3.0:
                recommendation = "平局"
                odds = match.odds_x
                confidence = "⭐"
                expected_return = f"+{((match.odds_x - 1) * 100):.1f}%"
                reason = "平局赔率较高，可考虑"
            else:
                recommendation = f"主胜 ({match.home_team})"
                odds = match.odds_1
                confidence = "⭐⭐"
                expected_return = f"+{((match.odds_1 - 1) * 100):.1f}%"
                reason = "保守选择"
            
            recommendations.append({
                'match': match,
                'recommendation': recommendation,
                'odds': odds,
                'confidence': confidence,
                'expected_return': expected_return,
                'reason': reason
            })
        
        # 按预期收益排序
        recommendations.sort(key=lambda x: x['odds'], reverse=True)
        return recommendations
    
    # 回调处理方法
    async def _handle_check_callback(self, query):
        """处理查看比赛回调"""
        await query.edit_message_text("🔄 正在获取最新比赛信息...")
        
        matches = await self._get_cached_matches(force_refresh=True)
        
        if not matches:
            await query.edit_message_text("😔 暂时没有找到即将开始的足球比赛。")
            return
        
        matches_text = "⚽ **即将开始的足球比赛** ⚽\n\n"
        for i, match in enumerate(matches[:8], 1):
            matches_text += f"{i}. {match.format_for_telegram()}\n\n"
        
        matches_text += f"\n📊 共 {len(matches)} 场比赛\n"
        matches_text += f"🕐 {datetime.now().strftime('%H:%M:%S')}"
        
        keyboard = [
            [InlineKeyboardButton("🔄 刷新", callback_data="refresh_matches")],
            [InlineKeyboardButton("📊 比较赔率", callback_data="compare_odds")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            matches_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
    
    async def _handle_compare_callback(self, query):
        """处理比较赔率回调"""
        matches = await self._get_cached_matches()
        
        if not matches:
            await query.edit_message_text("😔 暂时没有比赛数据可供比较。")
            return
        
        analysis = self._analyze_odds(matches)
        
        compare_text = "📊 **赔率比较** 📊\n\n"
        
        if analysis.get('best_home_win'):
            match = analysis['best_home_win']
            compare_text += f"🏆 最佳主胜: {match.home_team} ({match.odds_1})\n"
        
        if analysis.get('best_draw'):
            match = analysis['best_draw']
            compare_text += f"⚖️ 最佳平局: {match.home_team} vs {match.away_team} ({match.odds_x})\n"
        
        if analysis.get('best_away_win'):
            match = analysis['best_away_win']
            compare_text += f"🎯 最佳客胜: {match.away_team} ({match.odds_2})\n\n"
        
        compare_text += f"📈 平均赔率: {analysis.get('avg_odds_1', 0):.2f} / {analysis.get('avg_odds_x', 0):.2f} / {analysis.get('avg_odds_2', 0):.2f}"
        
        keyboard = [
            [InlineKeyboardButton("💡 投注建议", callback_data="bet_advice")],
            [InlineKeyboardButton("⚽ 查看比赛", callback_data="check_matches")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            compare_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
    
    async def _handle_bet_callback(self, query):
        """处理投注建议回调"""
        matches = await self._get_cached_matches()
        
        if not matches:
            await query.edit_message_text("😔 暂时没有比赛数据可供分析。")
            return
        
        recommendations = self._generate_bet_recommendations(matches)
        
        bet_text = "💡 **投注建议** 💡\n\n"
        
        for i, rec in enumerate(recommendations[:3], 1):
            bet_text += f"**{i}. {rec['match'].home_team} vs {rec['match'].away_team}**\n"
            bet_text += f"🎯 {rec['recommendation']} ({rec['odds']})\n"
            bet_text += f"⭐ {rec['confidence']} | 💰 {rec['expected_return']}\n\n"
        
        bet_text += "⚠️ 投注有风险，请理性投注！"
        
        keyboard = [
            [InlineKeyboardButton("🔄 刷新建议", callback_data="refresh_bet_advice")],
            [InlineKeyboardButton("📊 查看赔率", callback_data="compare_odds")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            bet_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
    
    async def _handle_help_callback(self, query):
        """处理帮助回调"""
        help_text = """
❓ **快速帮助** ❓

**主要功能：**
⚽ 查看足球比赛
📊 比较赔率分析
💡 获取投注建议
📋 查看系统状态

**使用提示：**
• 点击按钮快速操作
• 定期刷新获取最新数据
• 理性投注，量力而行

使用 /help 查看完整帮助信息。
        """
        
        keyboard = [
            [InlineKeyboardButton("⚽ 查看比赛", callback_data="check_matches")],
            [InlineKeyboardButton("📊 比较赔率", callback_data="compare_odds")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            help_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
    
    async def _handle_refresh_matches_callback(self, query):
        """处理刷新比赛回调"""
        await self._handle_check_callback(query)
    
    async def _handle_refresh_bet_callback(self, query):
        """处理刷新投注建议回调"""
        await self._handle_bet_callback(query)
    
    async def _handle_refresh_status_callback(self, query):
        """处理刷新状态回调"""
        cache_stats = await self.cache_manager.get_stats()
        active_users = len(self.user_sessions)
        
        status_text = "📋 **系统状态** 📋\n\n"
        status_text += f"🤖 机器人: ✅ 运行中\n"
        status_text += f"👥 活跃用户: {active_users}\n"
        status_text += f"💾 缓存: {cache_stats.get('entries', 0)} 条目\n"
        status_text += f"🕐 {datetime.now().strftime('%H:%M:%S')}"
        
        keyboard = [
            [InlineKeyboardButton("🔄 再次刷新", callback_data="refresh_status")],
            [InlineKeyboardButton("⚽ 查看比赛", callback_data="check_matches")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            status_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
    
    async def health_check(self) -> dict:
        """机器人健康检查"""
        try:
            health_status = {
                'status': 'healthy',
                'bot_initialized': self.application is not None,
                'active_users': len(self.user_sessions),
                'timestamp': datetime.now().isoformat()
            }
            
            # 检查机器人连接
            if self.application:
                try:
                    bot_info = await self.application.bot.get_me()
                    health_status['bot_info'] = {
                        'username': bot_info.username,
                        'first_name': bot_info.first_name,
                        'id': bot_info.id
                    }
                except Exception as e:
                    health_status['status'] = 'unhealthy'
                    health_status['bot_error'] = str(e)
            
            return health_status
            
        except Exception as e:
            return {
                'status': 'error',
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }
    
    async def run(self):
        """运行机器人（独立运行模式）"""
        try:
            await self.initialize()
            
            logger.info("启动机器人...")
            
            if hasattr(self.config.telegram, 'webhook_url') and self.config.telegram.webhook_url:
                # Webhook模式
                await self.application.bot.set_webhook(
                    url=self.config.telegram.webhook_url,
                    allowed_updates=Update.ALL_TYPES
                )
                logger.info(f"Webhook设置完成: {self.config.telegram.webhook_url}")
                # 在webhook模式下，需要手动启动和保持运行
                await self.application.initialize()
                await self.application.start()
                # 这里需要其他方式保持运行，比如web服务器
                import signal
                import sys
                
                def signal_handler(sig, frame):
                    logger.info('收到停止信号，正在关闭...')
                    sys.exit(0)
                
                signal.signal(signal.SIGINT, signal_handler)
                signal.signal(signal.SIGTERM, signal_handler)
                
                # 保持运行
                while True:
                    await asyncio.sleep(1)
            else:
                # 轮询模式 - 使用最简单的方式，避免触发 Updater
                logger.info("开始轮询模式")
                
                try:
                    # 手动初始化和启动，不使用 async with
                    await self.application.initialize()
                    await self.application.start()
                    
                    logger.info("机器人已启动，开始轮询...")
                    
                    # 创建轮询任务
                    async def polling_loop():
                        offset = 0
                        while True:
                            try:
                                # 获取更新
                                updates = await self.application.bot.get_updates(
                                    offset=offset,
                                    timeout=10,
                                    allowed_updates=['message', 'callback_query']
                                )
                                
                                # 处理更新
                                for update in updates:
                                    offset = update.update_id + 1
                                    # 将更新放入队列处理
                                    await self.application.process_update(update)
                                    
                            except Exception as e:
                                logger.error(f"轮询错误: {e}")
                                await asyncio.sleep(5)  # 错误时等待5秒
                    
                    # 启动轮询任务
                    polling_task = asyncio.create_task(polling_loop())
                    
                    # 等待停止信号
                    import signal
                    stop_event = asyncio.Event()
                    
                    def signal_handler(sig, frame):
                        logger.info('收到停止信号，正在关闭...')
                        stop_event.set()
                    
                    signal.signal(signal.SIGINT, signal_handler)
                    signal.signal(signal.SIGTERM, signal_handler)
                    
                    try:
                        await stop_event.wait()
                    except KeyboardInterrupt:
                        logger.info("收到中断信号，正在停止...")
                    finally:
                        polling_task.cancel()
                        try:
                            await polling_task
                        except asyncio.CancelledError:
                            pass
                        await self.application.stop()
                        await self.application.shutdown()
                        
                except Exception as e:
                    logger.error(f"轮询模式启动失败: {e}")
                    raise
            
        except Exception as e:
            logger.error(f"运行机器人时出错: {e}")
            raise


# 主函数
async def main():
    """主函数"""
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )
    
    bot = FootballBot()
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
