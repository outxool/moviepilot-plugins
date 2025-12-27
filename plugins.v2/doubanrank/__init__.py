import datetime
import re
import json
import time
from threading import Event, Thread
from typing import Tuple, List, Dict, Any

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.chain.download import DownloadChain
from app.chain.media import MediaChain
from app.chain.subscribe import SubscribeChain
from app.core.config import settings
from app.core.context import MediaInfo
from app.core.metainfo import MetaInfo
from app.log import logger
from app.plugins import _PluginBase
from app.utils.http import RequestUtils

# 兼容性导入
try:
    from app.schemas import MediaType, NotificationType
except ImportError:
    from app.schemas.types import MediaType, NotificationType

class DoubanRank(_PluginBase):
    # 插件基本信息
    plugin_name = "豆瓣榜单订阅（爬虫版）"
    plugin_desc = "直接抓取豆瓣官网数据（无需RSSHub），支持热门影视、Top250等榜单订阅。"
    plugin_icon = "https://img3.doubanio.com/favicon.ico"
    plugin_version = "3.0.0"
    plugin_author = "outxool"
    plugin_config_prefix = "doubanrank_"
    plugin_order = 6
    auth_level = 2

    _event = Event()
    _scheduler = None
    
    # 定义榜单类型映射
    # type: api (调用官方JSON接口) / html (爬取网页)
    _rank_config = {
        'movie_hot': {'name': '热门电影', 'type': 'api', 'url': 'https://movie.douban.com/j/search_subjects?type=movie&tag=%E7%83%AD%E9%97%A8&sort=recommend&page_limit=20&page_start=0', 'mtype': MediaType.MOVIE},
        'tv_hot': {'name': '热门电视剧', 'type': 'api', 'url': 'https://movie.douban.com/j/search_subjects?type=tv&tag=%E7%83%AD%E9%97%A8&sort=recommend&page_limit=20&page_start=0', 'mtype': MediaType.TV},
        'show_hot': {'name': '热门综艺', 'type': 'api', 'url': 'https://movie.douban.com/j/search_subjects?type=tv&tag=%E7%BB%BC%E8%89%BA&sort=recommend&page_limit=20&page_start=0', 'mtype': MediaType.TV},
        'movie_top250': {'name': '电影Top250', 'type': 'html', 'url': 'https://movie.douban.com/top250', 'mtype': MediaType.MOVIE},
        'movie_weekly': {'name': '一周口碑榜', 'type': 'html', 'url': 'https://movie.douban.com/chart', 'mtype': MediaType.MOVIE},
    }
    
    # 配置项
    _enabled = False
    _cron = "0 10 * * *"
    _onlyonce = False
    _ranks = []
    _vote = 0
    _clear = False
    _proxy = False

    def init_plugin(self, config: dict = None):
        if config:
            self._enabled = config.get("enabled", False)
            self._cron = config.get("cron", "0 10 * * *")
            self._proxy = config.get("proxy", False)
            self._onlyonce = config.get("onlyonce", False)
            self._vote = float(config.get("vote") or 0)
            self._ranks = config.get("ranks") or []
            self._clear = config.get("clear", False)

        self.stop_service()

        if self._enabled or self._onlyonce:
            if self._enabled and self._cron:
                self._scheduler = BackgroundScheduler(timezone=settings.TZ)
                self._scheduler.add_job(func=self.__refresh_douban, trigger=CronTrigger.from_crontab(self._cron), name="豆瓣榜单订阅")
                if self._scheduler.get_jobs():
                    self._scheduler.start()

            self.__execute_once_operations()

    def __execute_once_operations(self):
        config_updated = False
        
        if self._clear:
            self.save_data('history', [])
            self._clear = False
            config_updated = True
            logger.info("豆瓣榜单订阅：历史记录已清理")

        if self._onlyonce:
            logger.info("豆瓣榜单订阅：检测到立即运行指令，正在后台执行...")
            Thread(target=self.__refresh_douban).start()
            self._onlyonce = False
            config_updated = True

        if config_updated:
            self.__update_config()

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": "/delete_history",
                "endpoint": self.delete_history,
                "methods": ["GET"],
                "summary": "删除豆瓣榜单订阅历史记录"
            }
        ]

    def get_service(self) -> List[Dict[str, Any]]:
        if self._enabled and self._cron:
            return [{
                "id": "DoubanRank",
                "name": "豆瓣榜单订阅服务",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.__refresh_douban,
                "kwargs": {}
            }]
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [{'component': 'VSwitch', 'props': {'model': 'enabled', 'label': '启用插件'}}]},
                            {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [{'component': 'VSwitch', 'props': {'model': 'proxy', 'label': '使用代理服务器(仅当直连不通时开启)'}}]},
                            {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [{'component': 'VSwitch', 'props': {'model': 'onlyonce', 'label': '立即运行一次'}}]}
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VCronField', 'props': {'model': 'cron', 'label': '执行周期', 'placeholder': '5位cron表达式'}}]},
                            {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VTextField', 'props': {'model': 'vote', 'label': '最低评分', 'placeholder': '评分大于等于该值才订阅'}}]}
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {'component': 'VCol', 'content': [{'component': 'VSelect', 'props': {'chips': True, 'multiple': True, 'model': 'ranks', 'label': '选择榜单', 'items': [
                                {'title': '热门电影 (Hot Movies)', 'value': 'movie_hot'},
                                {'title': '热门电视剧 (Hot TV)', 'value': 'tv_hot'},
                                {'title': '热门综艺 (Hot Variety)', 'value': 'show_hot'},
                                {'title': '电影Top250 (前25名)', 'value': 'movie_top250'},
                                {'title': '一周口碑电影榜', 'value': 'movie_weekly'},
                            ]}}]}
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [{'component': 'VSwitch', 'props': {'model': 'clear', 'label': '清理历史记录'}}]}
                        ]
                    }
                ]
            }
        ], {
            "enabled": False, "cron": "0 10 * * *", "proxy": False, "onlyonce": False, "vote": "", 
            "ranks": [], "clear": False
        }

    def get_page(self) -> List[dict]:
        historys = self.get_data('history')
        if not historys:
            return [{'component': 'div', 'text': '暂无数据', 'props': {'class': 'text-center'}}]
        
        historys = sorted(historys, key=lambda x: x.get('time'), reverse=True)
        contents = []
        for history in historys:
            title = history.get("title")
            doubanid = history.get("doubanid")
            contents.append({
                'component': 'VCard',
                'props': {'class': 'mx-auto mb-2', 'width': '100%'},
                'content': [
                    {
                        "component": "VDialogCloseBtn",
                        "props": {'innerClass': 'absolute top-0 right-0'},
                        'events': {
                            'click': {
                                'api': 'plugin/DoubanRank/delete_history',
                                'method': 'get',
                                'params': {'key': f"doubanrank: {title} (DB:{doubanid})", 'apikey': settings.API_TOKEN}
                            }
                        },
                    },
                    {
                        'component': 'div',
                        'props': {'class': 'd-flex justify-space-start flex-nowrap flex-row'},
                        'content': [
                            {'component': 'div', 'content': [{'component': 'VImg', 'props': {'src': history.get("poster"), 'height': 120, 'width': 80, 'aspect-ratio': '2/3', 'class': 'object-cover shadow ring-gray-500', 'cover': True}}]},
                            {'component': 'div', 'content': [
                                {'component': 'VCardTitle', 'props': {'class': 'ps-1 pe-5 break-words whitespace-break-spaces'}, 'content': [{'component': 'a', 'props': {'href': f"https://movie.douban.com/subject/{doubanid}", 'target': '_blank'}, 'text': title}]},
                                {'component': 'VCardText', 'props': {'class': 'pa-0 px-2'}, 'text': f'类型：{history.get("type")}'},
                                {'component': 'VCardText', 'props': {'class': 'pa-0 px-2'}, 'text': f'时间：{history.get("time")}'}
                            ]}
                        ]
                    }
                ]
            })
        return [{'component': 'div', 'props': {'class': 'grid gap-3 grid-info-card'}, 'content': contents}]

    def stop_service(self):
        pass

    def delete_history(self, key: str, apikey: str):
        if apikey != settings.API_TOKEN:
            return {"success": False, "message": "API密钥错误"}
        historys = self.get_data('history') or []
        historys = [h for h in historys if h.get("unique") != key]
        self.save_data('history', historys)
        return {"success": True, "message": "删除成功"}

    def __update_config(self):
        self.update_config({
            "enabled": self._enabled, "cron": self._cron, "onlyonce": self._onlyonce,
            "vote": self._vote, "ranks": self._ranks, "clear": self._clear, "proxy": self._proxy
        })

    def __refresh_douban(self):
        logger.info(f"开始抓取豆瓣榜单 ...")
        if not self._ranks:
            logger.info("未选择任何榜单，任务结束")
            return

        history = self.get_data('history') or []
        
        # 遍历选中的榜单
        for rank_key in self._ranks:
            rank_conf = self._rank_config.get(rank_key)
            if not rank_conf: continue
            
            logger.info(f"正在获取榜单：{rank_conf['name']}")
            try:
                items = self.__get_douban_data(rank_conf)
                if not items:
                    logger.warning(f"榜单 {rank_conf['name']} 未获取到数据")
                    continue
                
                logger.info(f"榜单 {rank_conf['name']} 获取到 {len(items)} 条数据")
                
                for item in items:
                    if self._event.is_set(): return
                    
                    title = item.get('title')
                    douban_id = item.get('id')
                    vote = float(item.get('rate') or 0)
                    
                    # 评分过滤
                    if self._vote and vote < self._vote: continue
                    
                    unique_flag = f"doubanrank: {title} (DB:{douban_id})"
                    if unique_flag in [h.get("unique") for h in history]: continue
                    
                    # 元数据与媒体识别
                    meta = MetaInfo(title)
                    # 尝试从API数据中获取年份，如果是HTML爬取可能没有年份
                    if item.get('year'): meta.year = item.get('year')
                    meta.type = rank_conf['mtype']
                    
                    mediainfo = None
                    # 1. 尝试通过豆瓣ID识别 (更准)
                    if douban_id:
                        if settings.RECOGNIZE_SOURCE == "themoviedb":
                            # 这里调用 MP 的 MediaChain 尝试转换
                            try:
                                tmdbinfo = MediaChain().get_tmdbinfo_by_doubanid(doubanid=douban_id, mtype=meta.type)
                                if tmdbinfo:
                                    mediainfo = self.chain.recognize_media(meta=meta, tmdbid=tmdbinfo.get("id"))
                            except Exception:
                                pass
                        else:
                            mediainfo = self.chain.recognize_media(meta=meta, doubanid=douban_id)
                    
                    # 2. 如果豆瓣ID识别失败，尝试名称识别
                    if not mediainfo:
                        mediainfo = self.chain.recognize_media(meta=meta)
                        
                    if not mediainfo:
                        logger.warn(f'未识别到媒体信息: {title} (DB:{douban_id})')
                        continue
                    
                    # 存在性检查 (媒体库 + 订阅)
                    exist_flag, _ = DownloadChain().get_no_exists_info(meta=meta, mediainfo=mediainfo)
                    if exist_flag: 
                        logger.info(f'{mediainfo.title_year} 媒体库中已存在，跳过')
                        continue
                    
                    subscribechain = SubscribeChain()
                    if subscribechain.exists(mediainfo=mediainfo, meta=meta): 
                        logger.info(f'{mediainfo.title_year} 订阅已存在，跳过')
                        continue
                    
                    # 添加订阅
                    subscribechain.add(title=mediainfo.title, year=mediainfo.year, mtype=mediainfo.type, 
                                       tmdbid=mediainfo.tmdb_id, season=meta.begin_season, exist_ok=True, username="豆瓣榜单")
                    
                    history.append({
                        "title": title, "type": mediainfo.type.value, "year": mediainfo.year,
                        "poster": mediainfo.get_poster_image(), "overview": mediainfo.overview,
                        "tmdbid": mediainfo.tmdb_id, "doubanid": douban_id,
                        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "unique": unique_flag
                    })
                    
                    # 避免请求过快
                    time.sleep(1)
                    
            except Exception as e:
                logger.error(f"处理榜单 {rank_conf['name']} 出错: {e}")

        self.save_data('history', history)
        logger.info(f"所有豆瓣榜单处理完成")

    def __get_douban_data(self, rank_conf) -> List[dict]:
        """
        获取豆瓣数据 (JSON API 或 HTML Regex)
        """
        url = rank_conf['url']
        rtype = rank_conf['type']
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://movie.douban.com/"
        }
        
        req_proxy = settings.PROXY if (self._proxy and settings.PROXY) else None
        if req_proxy:
            req = RequestUtils(proxies=req_proxy)
        else:
            req = RequestUtils()
            
        try:
            res = req.get_res(url, headers=headers)
            if not res or res.status_code != 200:
                logger.error(f"请求豆瓣失败: {url} (Code: {res.status_code if res else 'None'})")
                return []
            
            results = []
            
            # 模式1: JSON API (热门影视)
            if rtype == 'api':
                try:
                    data = res.json()
                    # 豆瓣API返回结构: {"subjects": [...]}
                    subjects = data.get('subjects', [])
                    for sub in subjects:
                        results.append({
                            'title': sub.get('title'),
                            'rate': sub.get('rate'),
                            'id': sub.get('id'),
                            'url': sub.get('url'),
                            'is_new': sub.get('is_new', False)
                        })
                except json.JSONDecodeError:
                    logger.error("豆瓣API返回非JSON格式")
            
            # 模式2: HTML Regex (Top250 / Weekly)
            elif rtype == 'html':
                html = res.text
                if 'movie_top250' in url:
                    # 匹配Top250 HTML结构
                    # <div class="hd">\s*<a href="https://movie.douban.com/subject/1292052/" class="">\s*<span class="title">肖申克的救赎</span>
                    # <span class="rating_num" property="v:average">9.7</span>
                    pattern = re.compile(r'class="hd">\s*<a href="https://movie\.douban\.com/subject/(\d+)/".*?<span class="title">([^<]+)</span>.*?<span class="rating_num"[^>]*>([\d\.]+)</span>', re.S)
                    matches = pattern.findall(html)
                    for m in matches:
                        results.append({
                            'id': m[0],
                            'title': m[1],
                            'rate': m[2]
                        })
                elif 'chart' in url:
                    # 匹配口碑榜 HTML结构
                    # <a class="nbg" href="https://movie.douban.com/subject/35928522/"  title="奥本海默">
                    # <span class="rating_nums">8.8</span>
                    pattern = re.compile(r'<a class="nbg" href="https://movie\.douban\.com/subject/(\d+)/"\s*title="([^"]+)".*?<span class="rating_nums">([\d\.]+)</span>', re.S)
                    matches = pattern.findall(html)
                    for m in matches:
                        results.append({
                            'id': m[0],
                            'title': m[1],
                            'rate': m[2]
                        })
            
            return results
            
        except Exception as e:
            logger.error(f"解析豆瓣数据失败: {e}")
            return []
