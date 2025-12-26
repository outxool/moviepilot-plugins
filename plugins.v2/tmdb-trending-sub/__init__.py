"""
TMDB趋势自动订阅插件
自动订阅TMDB热门/趋势的电影、电视剧、动画等内容
"""
import datetime
from threading import Lock
from typing import Optional, Any, List, Dict, Tuple

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.core.context import MediaInfo
from app.core.metainfo import MetaInfo
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import MediaType, NotificationType, EventType
from app.chain.subscribe import SubscribeChain
from app.chain.download import DownloadChain

lock = Lock()


class TMDbTrendingSub(_PluginBase):
    """TMDB趋势自动订阅插件"""
    
    # 插件名称
    plugin_name = "TMDB趋势订阅"
    # 插件描述
    plugin_desc = "自动订阅TMDB热门/趋势的电影、电视剧、动画等内容"
    # 插件图标
    plugin_icon = "https://www.themoviedb.org/assets/2/favicon-32x32-543a21832c8931d3494a68881f6afcafc58e96c5d324345377f3197a37b367b5.png"
    # 插件版本
    plugin_version = "1.0.0"
    # 插件作者
    plugin_author = "PluginCreator"
    # 作者主页
    author_url = "https://github.com"
    # 插件配置项ID前缀
    plugin_config_prefix = "tmdbtrendingsub_"
    # 加载顺序
    plugin_order = 25
    # 可使用的用户级别
    auth_level = 1
    
    # 私有属性
    _scheduler: Optional[BackgroundScheduler] = None
    _subscribechain: SubscribeChain = None
    _downloadchain: DownloadChain = None
    
    # 配置属性
    _enabled: bool = False
    _cron: str = "0 9 * * *"
    _onlyonce: bool = False
    _notify: bool = True
    _clear: bool = False
    _tmdb_api_key: str = ""
    _min_score: float = 6.0
    _min_votes: int = 100
    
    # 分类配置
    _movie_enabled: bool = True
    _tv_enabled: bool = True
    _anime_enabled: bool = True
    _documentary_enabled: bool = False
    
    # 类型配置
    _movie_trending: bool = True
    _movie_popular: bool = True
    _movie_top_rated: bool = True
    _movie_now_playing: bool = False
    _movie_upcoming: bool = False
    
    _tv_trending: bool = True
    _tv_popular: bool = True
    _tv_top_rated: bool = True
    _tv_on_the_air: bool = False
    _tv_airing_today: bool = False
    
    _anime_trending: bool = True
    _anime_popular: bool = True
    
    _documentary_trending: bool = True
    _documentary_popular: bool = True
    
    # 数量配置
    _movie_count: int = 10
    _tv_count: int = 10
    _anime_count: int = 5
    _documentary_count: int = 5

    def init_plugin(self, config: dict = None):
        """初始化插件"""
        self._subscribechain = SubscribeChain()
        self._downloadchain = DownloadChain()

        if config:
            self._enabled = config.get("enabled", False)
            self._cron = config.get("cron", "0 9 * * *")
            self._onlyonce = config.get("onlyonce", False)
            self._notify = config.get("notify", True)
            self._clear = config.get("clear", False)
            self._tmdb_api_key = config.get("tmdb_api_key", "")
            self._min_score = float(config.get("min_score", 6.0))
            self._min_votes = int(config.get("min_votes", 100))

            # 分类开关
            self._movie_enabled = config.get("movie_enabled", True)
            self._tv_enabled = config.get("tv_enabled", True)
            self._anime_enabled = config.get("anime_enabled", True)
            self._documentary_enabled = config.get("documentary_enabled", False)

            # 电影类型
            self._movie_trending = config.get("movie_trending", True)
            self._movie_popular = config.get("movie_popular", True)
            self._movie_top_rated = config.get("movie_top_rated", True)
            self._movie_now_playing = config.get("movie_now_playing", False)
            self._movie_upcoming = config.get("movie_upcoming", False)

            # 电视剧类型
            self._tv_trending = config.get("tv_trending", True)
            self._tv_popular = config.get("tv_popular", True)
            self._tv_top_rated = config.get("tv_top_rated", True)
            self._tv_on_the_air = config.get("tv_on_the_air", False)
            self._tv_airing_today = config.get("tv_airing_today", False)

            # 动画类型
            self._anime_trending = config.get("anime_trending", True)
            self._anime_popular = config.get("anime_popular", True)

            # 纪录片类型
            self._documentary_trending = config.get("documentary_trending", True)
            self._documentary_popular = config.get("documentary_popular", True)

            # 数量配置
            self._movie_count = int(config.get("movie_count", 10))
            self._tv_count = int(config.get("tv_count", 10))
            self._anime_count = int(config.get("anime_count", 5))
            self._documentary_count = int(config.get("documentary_count", 5))

        # 停止现有任务
        self.stop_service()

        # 清理插件历史
        if self._clear:
            self.del_data(key="history")
            self._clear = False
            self.__update_config()
            logger.info("历史清理完成")

        if self._enabled or self._onlyonce:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            
            # 周期执行
            if self._cron:
                logger.info(f"TMDB趋势订阅服务启动，周期：{self._cron}")
                try:
                    self._scheduler.add_job(func=self.__refresh_tmdb,
                                            trigger=CronTrigger.from_crontab(self._cron),
                                            name="TMDB趋势订阅")
                except Exception as e:
                    logger.error(f"TMDB趋势订阅服务启动失败，错误信息：{str(e)}")
                    self.systemmessage.put(f"TMDB趋势订阅服务启动失败，错误信息：{str(e)}")
            else:
                self._scheduler.add_job(func=self.__refresh_tmdb, 
                                        trigger=CronTrigger.from_crontab("0 9 * * *"),
                                        name="TMDB趋势订阅")
                logger.info("TMDB趋势订阅服务启动，周期：每天 09:00")

            # 一次性执行
            if self._onlyonce:
                logger.info("TMDB趋势订阅服务启动，立即运行一次")
                self._scheduler.add_job(func=self.__refresh_tmdb, trigger='date',
                                        run_date=datetime.datetime.now(
                                            tz=pytz.timezone(settings.TZ)) + datetime.timedelta(seconds=3)
                                        )
                # 关闭一次性开关
                self._onlyonce = False
                # 保存配置
                self.__update_config()

            if self._scheduler.get_jobs():
                # 启动服务
                self._scheduler.print_jobs()
                self._scheduler.start()

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """定义远程控制命令"""
        return [{
            "cmd": "/tmdb_sync",
            "event": EventType.PluginAction,
            "desc": "TMDB趋势订阅同步",
            "category": "订阅",
            "data": {
                "action": "tmdb_sync"
            }
        }]

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """拼装插件配置页面"""
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'onlyonce',
                                            'label': '立即运行一次',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'notify',
                                            'label': '发送通知',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'tmdb_api_key',
                                            'label': 'TMDB API Key',
                                            'placeholder': '请输入TMDB API Key'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VCronField',
                                        'props': {
                                            'model': 'cron',
                                            'label': '执行周期',
                                            'placeholder': '0 9 * * *'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'min_score',
                                            'label': '最低评分',
                                            'type': 'number',
                                            'step': 0.1,
                                            'min': 0,
                                            'max': 10
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'min_votes',
                                            'label': '最少评价数',
                                            'type': 'number',
                                            'min': 0
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '分类配置：每个分类可单独设置订阅条数'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'movie_enabled',
                                            'label': '电影',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'movie_count',
                                            'label': '电影订阅条数',
                                            'type': 'number',
                                            'min': 1,
                                            'max': 50
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'tv_enabled',
                                            'label': '电视剧',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'tv_count',
                                            'label': '电视剧订阅条数',
                                            'type': 'number',
                                            'min': 1,
                                            'max': 50
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'anime_enabled',
                                            'label': '动画',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'anime_count',
                                            'label': '动画订阅条数',
                                            'type': 'number',
                                            'min': 1,
                                            'max': 50
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'documentary_enabled',
                                            'label': '纪录片',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'documentary_count',
                                            'label': '纪录片订阅条数',
                                            'type': 'number',
                                            'min': 1,
                                            'max': 50
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '榜单类型：选择要订阅的榜单类型'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'movie_trending',
                                            'label': '电影趋势榜',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'movie_popular',
                                            'label': '电影热门榜',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'movie_top_rated',
                                            'label': '电影高分榜',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'movie_now_playing',
                                            'label': '正在上映',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'movie_upcoming',
                                            'label': '即将上映',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'tv_trending',
                                            'label': '剧集趋势榜',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'tv_popular',
                                            'label': '剧集热门榜',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'tv_top_rated',
                                            'label': '剧集高分榜',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'tv_on_the_air',
                                            'label': '正在播出',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'tv_airing_today',
                                            'label': '今日播出',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'anime_trending',
                                            'label': '动画趋势榜',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'anime_popular',
                                            'label': '动画热门榜',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'documentary_trending',
                                            'label': '纪录片趋势榜',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'documentary_popular',
                                            'label': '纪录片热门榜',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                props={
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'clear',
                                            'label': '清理历史记录',
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "onlyonce": False,
            "notify": True,
            "clear": False,
            "cron": "0 9 * * *",
            "tmdb_api_key": "",
            "min_score": 6.0,
            "min_votes": 100,
            "movie_enabled": True,
            "tv_enabled": True,
            "anime_enabled": True,
            "documentary_enabled": False,
            "movie_trending": True,
            "movie_popular": True,
            "movie_top_rated": True,
            "movie_now_playing": False,
            "movie_upcoming": False,
            "tv_trending": True,
            "tv_popular": True,
            "tv_top_rated": True,
            "tv_on_the_air": False,
            "tv_airing_today": False,
            "anime_trending": True,
            "anime_popular": True,
            "documentary_trending": True,
            "documentary_popular": True,
            "movie_count": 10,
            "tv_count": 10,
            "anime_count": 5,
            "documentary_count": 5
        }

    def get_page(self) -> List[dict]:
        """
        拼装插件详情页面，需要返回页面配置，同时附带数据
        """
        # 查询历史记录
        historys = self.get_data('history')
        if not historys:
            return [
                {
                    'component': 'div',
                    'text': '暂无数据',
                    'props': {
                        'class': 'text-center',
                    }
                }
            ]
        # 数据按时间降序排序
        historys = sorted(historys, key=lambda x: x.get('time'), reverse=True)
        # 拼装页面
        contents = []
        for history in historys:
            title = history.get("title")
            poster = history.get("poster")
            mtype = history.get("type")
            time_str = history.get("time")
            tmdb_id = history.get("tmdbid")
            vote_average = history.get("vote_average")
            vote_count = history.get("vote_count")
            category = history.get("category")
            
            if mtype == MediaType.TV.value:
                href = f"https://www.themoviedb.org/tv/{tmdb_id}"
            else:
                href = f"https://www.themoviedb.org/movie/{tmdb_id}"
            
            contents.append(
                {
                    'component': 'VCard',
                    'content': [
                        {
                            'component': 'div',
                            'props': {
                                'class': 'd-flex justify-space-start flex-nowrap flex-row',
                            },
                            'content': [
                                {
                                    'component': 'div',
                                    'content': [
                                        {
                                            'component': 'VImg',
                                            'props': {
                                                'src': poster,
                                                'height': 120,
                                                'width': 80,
                                                'aspect-ratio': '2/3',
                                                'class': 'object-cover shadow ring-gray-500',
                                                'cover': True
                                            }
                                        }
                                    ]
                                },
                                {
                                    'component': 'div',
                                    'content': [
                                        {
                                            'component': 'VCardSubtitle',
                                            'props': {
                                                'class': 'pa-2 font-bold break-words whitespace-break-spaces'
                                            },
                                            'content': [
                                                {
                                                    'component': 'a',
                                                    'props': {
                                                        'href': href,
                                                        'target': '_blank'
                                                    },
                                                    'text': title
                                                }
                                            ]
                                        },
                                        {
                                            'component': 'VCardText',
                                            'props': {
                                                'class': 'pa-0 px-2'
                                            },
                                            'text': f'评分：{vote_average}/10（{vote_count}评价）'
                                        },
                                        {
                                            'component': 'VCardText',
                                            'props': {
                                                'class': 'pa-0 px-2'
                                            },
                                            'text': f'分类：{category}'
                                        },
                                        {
                                            'component': 'VCardText',
                                            'props': {
                                                'class': 'pa-0 px-2'
                                            },
                                            'text': f'类型：{mtype}'
                                        },
                                        {
                                            'component': 'VCardText',
                                            'props': {
                                                'class': 'pa-0 px-2'
                                            },
                                            'text': f'订阅时间：{time_str}'
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            )

        return [
            {
                'component': 'div',
                'props': {
                    'class': 'grid gap-3 grid-info-card',
                },
                'content': contents
            }
        ]

    def stop_service(self):
        """
        停止服务
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            print(str(e))

    def __update_config(self):
        """
        更新配置
        """
        self.update_config({
            "enabled": self._enabled,
            "cron": self._cron,
            "onlyonce": self._onlyonce,
            "notify": self._notify,
            "clear": self._clear,
            "tmdb_api_key": self._tmdb_api_key,
            "min_score": self._min_score,
            "min_votes": self._min_votes,
            "movie_enabled": self._movie_enabled,
            "tv_enabled": self._tv_enabled,
            "anime_enabled": self._anime_enabled,
            "documentary_enabled": self._documentary_enabled,
            "movie_trending": self._movie_trending,
            "movie_popular": self._movie_popular,
            "movie_top_rated": self._movie_top_rated,
            "movie_now_playing": self._movie_now_playing,
            "movie_upcoming": self._movie_upcoming,
            "tv_trending": self._tv_trending,
            "tv_popular": self._tv_popular,
            "tv_top_rated": self._tv_top_rated,
            "tv_on_the_air": self._tv_on_the_air,
            "tv_airing_today": self._tv_airing_today,
            "anime_trending": self._anime_trending,
            "anime_popular": self._anime_popular,
            "documentary_trending": self._documentary_trending,
            "documentary_popular": self._documentary_popular,
            "movie_count": self._movie_count,
            "tv_count": self._tv_count,
            "anime_count": self._anime_count,
            "documentary_count": self._documentary_count,
        })

    def __refresh_tmdb(self):
        """
        刷新TMDB趋势数据
        """
        logger.info(f"开始刷新TMDB趋势...")
        
        if not self._tmdb_api_key:
            logger.error("未配置TMDB API Key")
            if self._notify:
                self.post_message(
                    mtype=NotificationType.Manual,
                    title="【TMDB趋势订阅】配置错误",
                    text="请先配置TMDB API Key"
                )
            return
        
        # 获取当前日期时间
        current_time = datetime.datetime.now()
        
        # 获取历史记录
        history: List[dict] = self.get_data('history') or []
        
        # 发送开始通知
        if self._notify:
            self.post_message(
                mtype=NotificationType.Plugin,
                title="【TMDB趋势订阅】开始执行",
                text="正在获取TMDB趋势数据并添加订阅..."
            )
        
        total_subscribed = 0
        category_stats = {}
        
        # 处理电影
        if self._movie_enabled:
            movie_count = self.__process_movies(history)
            if movie_count > 0:
                category_stats["电影"] = movie_count
                total_subscribed += movie_count
        
        # 处理电视剧
        if self._tv_enabled:
            tv_count = self.__process_tv(history)
            if tv_count > 0:
                category_stats["电视剧"] = tv_count
                total_subscribed += tv_count
        
        # 处理动画
        if self._anime_enabled:
            anime_count = self.__process_anime(history)
            if anime_count > 0:
                category_stats["动画"] = anime_count
                total_subscribed += anime_count
        
        # 处理纪录片
        if self._documentary_enabled:
            documentary_count = self.__process_documentary(history)
            if documentary_count > 0:
                category_stats["纪录片"] = documentary_count
                total_subscribed += documentary_count
        
        # 保存历史记录（保留最近500条）
        self.save_data('history', history[-500:])
        
        # 发送完成通知
        if self._notify:
            if total_subscribed > 0:
                stats_text = "\n".join([f"{cat}: {count}" for cat, count in category_stats.items()])
                self.post_message(
                    mtype=NotificationType.Plugin,
                    title="【TMDB趋势订阅】执行完成",
                    text=f"成功添加 {total_subscribed} 个订阅\n\n分类统计:\n{stats_text}"
                )
            else:
                self.post_message(
                    mtype=NotificationType.Plugin,
                    title="【TMDB趋势订阅】执行完成",
                    text="本次同步未添加新的订阅"
                )
        
        logger.info(f"TMDB趋势订阅刷新完成，共添加 {total_subscribed} 个订阅")

    def __process_movies(self, history: List[dict]) -> int:
        """处理电影订阅"""
        try:
            import requests
        except ImportError:
            logger.error("requests库未安装")
            return 0
        
        subscribed_count = 0
        endpoints = []
        
        # 添加启用的端点
        if self._movie_trending:
            endpoints.append(("trending", "trending/movie/day"))
        if self._movie_popular:
            endpoints.append(("popular", "movie/popular"))
        if self._movie_top_rated:
            endpoints.append(("top_rated", "movie/top_rated"))
        if self._movie_now_playing:
            endpoints.append(("now_playing", "movie/now_playing"))
        if self._movie_upcoming:
            endpoints.append(("upcoming", "movie/upcoming"))
        
        for list_type, endpoint in endpoints:
            try:
                url = f"https://api.themoviedb.org/3/{endpoint}"
                headers = {
                    "Authorization": f"Bearer {self._tmdb_api_key}",
                    "Content-Type": "application/json"
                }
                
                response = requests.get(url, headers=headers, timeout=30)
                if response.status_code == 200:
                    data = response.json()
                    movies = data.get("results", [])
                    
                    # 过滤和排序
                    filtered_movies = []
                    for movie in movies:
                        vote_average = movie.get("vote_average", 0)
                        vote_count = movie.get("vote_count", 0)
                        
                        if (vote_average >= self._min_score and 
                            vote_count >= self._min_votes and 
                            not movie.get("adult", False)):
                            filtered_movies.append(movie)
                    
                    # 按评分排序
                    filtered_movies.sort(key=lambda x: x.get("vote_average", 0), reverse=True)
                    
                    # 取前N个
                    movies_to_process = filtered_movies[:self._movie_count]
                    
                    # 处理每个电影
                    for movie in movies_to_process:
                        subscribed = self.__process_item(
                            item=movie,
                            category=f"电影-{list_type}",
                            media_type=MediaType.MOVIE,
                            history=history
                        )
                        if subscribed:
                            subscribed_count += 1
                            
            except Exception as e:
                logger.error(f"获取电影数据失败（{list_type}）: {str(e)}")
        
        return subscribed_count

    def __process_tv(self, history: List[dict]) -> int:
        """处理电视剧订阅"""
        try:
            import requests
        except ImportError:
            logger.error("requests库未安装")
            return 0
        
        subscribed_count = 0
        endpoints = []
        
        # 添加启用的端点
        if self._tv_trending:
            endpoints.append(("trending", "trending/tv/day"))
        if self._tv_popular:
            endpoints.append(("popular", "tv/popular"))
        if self._tv_top_rated:
            endpoints.append(("top_rated", "tv/top_rated"))
        if self._tv_on_the_air:
            endpoints.append(("on_the_air", "tv/on_the_air"))
        if self._tv_airing_today:
            endpoints.append(("airing_today", "tv/airing_today"))
        
        for list_type, endpoint in endpoints:
            try:
                url = f"https://api.themoviedb.org/3/{endpoint}"
                headers = {
                    "Authorization": f"Bearer {self._tmdb_api_key}",
                    "Content-Type": "application/json"
                }
                
                response = requests.get(url, headers=headers, timeout=30)
                if response.status_code == 200:
                    data = response.json()
                    tv_shows = data.get("results", [])
                    
                    # 过滤和排序
                    filtered_tv = []
                    for tv in tv_shows:
                        vote_average = tv.get("vote_average", 0)
                        vote_count = tv.get("vote_count", 0)
                        
                        if (vote_average >= self._min_score and 
                            vote_count >= self._min_votes):
                            filtered_tv.append(tv)
                    
                    # 按评分排序
                    filtered_tv.sort(key=lambda x: x.get("vote_average", 0), reverse=True)
                    
                    # 取前N个
                    tv_to_process = filtered_tv[:self._tv_count]
                    
                    # 处理每个电视剧
                    for tv in tv_to_process:
                        subscribed = self.__process_item(
                            item=tv,
                            category=f"电视剧-{list_type}",
                            media_type=MediaType.TV,
                            history=history
                        )
                        if subscribed:
                            subscribed_count += 1
                            
            except Exception as e:
                logger.error(f"获取电视剧数据失败（{list_type}）: {str(e)}")
        
        return subscribed_count

    def __process_anime(self, history: List[dict]) -> int:
        """处理动画订阅"""
        try:
            import requests
        except ImportError:
            logger.error("requests库未安装")
            return 0
        
        subscribed_count = 0
        
        # 获取动画数据（通过分类ID过滤）
        try:
            # 获取动画电影
            if self._anime_trending:
                url = "https://api.themoviedb.org/3/trending/movie/day"
                headers = {
                    "Authorization": f"Bearer {self._tmdb_api_key}",
                    "Content-Type": "application/json"
                }
                
                response = requests.get(url, headers=headers, timeout=30)
                if response.status_code == 200:
                    data = response.json()
                    all_movies = data.get("results", [])
                    
                    # 过滤动画（分类ID 16是动画）
                    anime_movies = []
                    for movie in all_movies:
                        if 16 in movie.get("genre_ids", []):
                            vote_average = movie.get("vote_average", 0)
                            vote_count = movie.get("vote_count", 0)
                            
                            if (vote_average >= self._min_score and 
                                vote_count >= self._min_votes and 
                                not movie.get("adult", False)):
                                anime_movies.append(movie)
                    
                    # 按评分排序
                    anime_movies.sort(key=lambda x: x.get("vote_average", 0), reverse=True)
                    
                    # 取前N个
                    anime_to_process = anime_movies[:self._anime_count]
                    
                    # 处理每个动画
                    for anime in anime_to_process:
                        subscribed = self.__process_item(
                            item=anime,
                            category="动画-trending",
                            media_type=MediaType.MOVIE,
                            history=history
                        )
                        if subscribed:
                            subscribed_count += 1
        
        except Exception as e:
            logger.error(f"获取动画数据失败: {str(e)}")
        
        return subscribed_count

    def __process_documentary(self, history: List[dict]) -> int:
        """处理纪录片订阅"""
        try:
            import requests
        except ImportError:
            logger.error("requests库未安装")
            return 0
        
        subscribed_count = 0
        
        # 获取纪录片数据（通过分类ID过滤）
        try:
            # 获取纪录片
            if self._documentary_trending:
                url = "https://api.themoviedb.org/3/trending/movie/day"
                headers = {
                    "Authorization": f"Bearer {self._tmdb_api_key}",
                    "Content-Type": "application/json"
                }
                
                response = requests.get(url, headers=headers, timeout=30)
                if response.status_code == 200:
                    data = response.json()
                    all_movies = data.get("results", [])
                    
                    # 过滤纪录片（分类ID 99是纪录片）
                    documentaries = []
                    for movie in all_movies:
                        if 99 in movie.get("genre_ids", []):
                            vote_average = movie.get("vote_average", 0)
                            vote_count = movie.get("vote_count", 0)
                            
                            if (vote_average >= self._min_score and 
                                vote_count >= self._min_votes and 
                                not movie.get("adult", False)):
                                documentaries.append(movie)
                    
                    # 按评分排序
                    documentaries.sort(key=lambda x: x.get("vote_average", 0), reverse=True)
                    
                    # 取前N个
                    docs_to_process = documentaries[:self._documentary_count]
                    
                    # 处理每个纪录片
                    for doc in docs_to_process:
                        subscribed = self.__process_item(
                            item=doc,
                            category="纪录片-trending",
                            media_type=MediaType.MOVIE,
                            history=history
                        )
                        if subscribed:
                            subscribed_count += 1
        
        except Exception as e:
            logger.error(f"获取纪录片数据失败: {str(e)}")
        
        return subscribed_count

    def __process_item(self, item: dict, category: str, media_type: MediaType, history: List[dict]) -> bool:
        """处理单个项目"""
        try:
            # 获取标题
            title = item.get("title") or item.get("name", "")
            if not title:
                return False
            
            # 检查是否已订阅
            unique_flag = f"tmdb:{item.get('id')}"
            if any(h.get("unique") == unique_flag for h in history):
                logger.debug(f"已订阅过: {title}")
                return False
            
            # 创建元数据
            meta = MetaInfo(title)
            if media_type == MediaType.MOVIE:
                release_date = item.get("release_date")
                if release_date:
                    meta.year = release_date.split("-")[0]
            else:
                first_air_date = item.get("first_air_date")
                if first_air_date:
                    meta.year = first_air_date.split("-")[0]
            
            # 识别媒体信息
            mediainfo: MediaInfo = self.chain.recognize_media(meta=meta, mtype=media_type)
            if not mediainfo:
                logger.warning(f"未识别到媒体信息: {title}")
                return False
            
            # 检查是否已存在订阅
            if self._subscribechain.exists(mediainfo=mediainfo, meta=meta):
                logger.debug(f"订阅已存在: {title}")
                return False
            
            # 添加订阅
            season = meta.begin_season if media_type == MediaType.TV else None
            self._subscribechain.add(
                title=mediainfo.title,
                year=mediainfo.year,
                mtype=mediainfo.type,
                tmdbid=mediainfo.tmdb_id,
                season=season,
                exist_ok=True,
                username="TMDB趋势订阅"
            )
            
            # 保存到历史记录
            history.append({
                "title": title,
                "type": mediainfo.type.value,
                "tmdbid": mediainfo.tmdb_id,
                "poster": f"https://image.tmdb.org/t/p/w500{item.get('poster_path', '')}",
                "vote_average": item.get("vote_average", 0),
                "vote_count": item.get("vote_count", 0),
                "category": category,
                "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "unique": unique_flag
            })
            
            logger.info(f"成功添加订阅: {title}")
            
            # 发送通知
            if self._notify:
                self.post_message(
                    mtype=NotificationType.Plugin,
                    title="【TMDB趋势订阅】新订阅",
                    text=f"{mediainfo.title_year}\n评分: {item.get('vote_average', 0)}/10\n分类: {category}"
                )
            
            return True
            
        except Exception as e:
            logger.error(f"处理项目失败: {str(e)}")
            return False