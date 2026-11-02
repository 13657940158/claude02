"""内置书目种子数据：12 本公版/经典书，供推荐与解析使用。

字段与 books 表一致；正文不在此处，由 CORPUS_DIR 中的文件补齐。
"""

SEED_BOOKS: list[dict] = [
    {
        "title": "小王子",
        "author": "安托万·德·圣埃克苏佩里",
        "category": "文学",
        "tags": ["童话", "哲理", "温柔", "短篇", "关系"],
        "intro": (
            "一位来自 B-612 小行星的王子离开玫瑰，途经六颗星球来到地球，"
            "在沙漠中与飞行员相遇。故事用孩子的眼睛重新提问：什么才是真正重要的？"
            "适合想从忙碌中慢下来、重新看待关系与责任的人。"
        ),
        "difficulty": 1,
        "total_minutes": 90,
        "rating": 95,
        "publish_year": 1943,
        "goals": ["emotion", "relationship", "growth"],
        "moods": ["温柔", "治愈", "哲思"],
    },
    {
        "title": "活着",
        "author": "余华",
        "category": "文学",
        "tags": ["小说", "生命", "苦难", "家庭", "中国当代"],
        "intro": (
            "福贵经历了从富家子弟到一无所有的一生，亲人一个个离开，他仍牵着老牛继续活下去。"
            "这本书不提供廉价的安慰，而是让人看清：活着本身就是一种坚韧。"
            "适合正在经历挫折、想重新理解'坚持'的人。"
        ),
        "difficulty": 2,
        "total_minutes": 240,
        "rating": 94,
        "publish_year": 1993,
        "goals": ["emotion", "knowledge", "growth"],
        "moods": ["沉重", "深刻", "震撼"],
    },
    {
        "title": "人类简史",
        "author": "尤瓦尔·赫拉利",
        "category": "历史",
        "tags": ["历史", "人类学", "认知革命", "社会", "科普"],
        "intro": (
            "从认知革命、农业革命到科学革命，赫拉利用一条大线索讲清智人如何成为地球的主宰。"
            "它最大的价值不是给出结论，而是提供一种俯瞰文明的视角。"
            "适合想建立宏观知识框架、喜欢追问'为什么'的读者。"
        ),
        "difficulty": 4,
        "total_minutes": 600,
        "rating": 91,
        "publish_year": 2011,
        "goals": ["knowledge", "growth", "career"],
        "moods": ["开阔", "理性", "思辨"],
    },
    {
        "title": "原子习惯",
        "author": "詹姆斯·克利尔",
        "category": "成长",
        "tags": ["习惯", "行为设计", "自我管理", "实用", "方法论"],
        "intro": (
            "每天改进 1%，一年后你会强大 37 倍。作者把习惯拆成提示、渴求、反应、奖励四步，"
            "并给出可操作的环境设计方法。适合想养成阅读、运动或早睡习惯的人。"
        ),
        "difficulty": 2,
        "total_minutes": 260,
        "rating": 88,
        "publish_year": 2018,
        "goals": ["habit", "growth", "career"],
        "moods": ["务实", "激励", "清晰"],
    },
    {
        "title": "被讨厌的勇气",
        "author": "岸见一郎、古贺史健",
        "category": "心理",
        "tags": ["阿德勒", "课题分离", "人际关系", "自我接纳", "对话体"],
        "intro": (
            "用青年与哲人的对话，讲透阿德勒心理学的核心：一切烦恼都来自人际关系，"
            "而自由意味着拥有被讨厌的勇气。适合在意他人评价、内耗较多的人。"
        ),
        "difficulty": 2,
        "total_minutes": 220,
        "rating": 90,
        "publish_year": 2013,
        "goals": ["emotion", "relationship", "growth"],
        "moods": ["清醒", "鼓励", "顿悟"],
    },
    {
        "title": "思考，快与慢",
        "author": "丹尼尔·卡尼曼",
        "category": "思维",
        "tags": ["认知偏差", "决策", "心理学", "行为经济学", "硬书"],
        "intro": (
            "诺奖得主卡尼曼把大脑分成快系统与慢系统，系统梳理锚定效应、损失厌恶等偏差。"
            "读完你会对自己的判断多一分怀疑，也多一分可靠。适合做决策、做产品、做投资的人。"
        ),
        "difficulty": 5,
        "total_minutes": 720,
        "rating": 93,
        "publish_year": 2011,
        "goals": ["knowledge", "career", "growth"],
        "moods": ["烧脑", "严谨", "颠覆"],
    },
    {
        "title": "原则",
        "author": "瑞·达利欧",
        "category": "商业",
        "tags": ["决策", "管理", "投资", "组织", "经验"],
        "intro": (
            "桥水创始人把几十年踩过的坑写成一套可复用的原则：极度透明、可信度加权、"
            "痛苦 + 反思 = 进步。适合处于职业上升期、想建立自己决策体系的人。"
        ),
        "difficulty": 4,
        "total_minutes": 600,
        "rating": 86,
        "publish_year": 2017,
        "goals": ["career", "growth", "knowledge"],
        "moods": ["冷静", "务实", "进取"],
    },
    {
        "title": "非暴力沟通",
        "author": "马歇尔·卢森堡",
        "category": "沟通",
        "tags": ["沟通", "共情", "冲突", "观察与感受", "关系"],
        "intro": (
            "把沟通拆成观察、感受、需要、请求四步，帮助你在冲突中不攻击、不逃离，"
            "既能表达自己，也能听见对方。适合亲密关系、亲子沟通与职场协作。"
        ),
        "difficulty": 2,
        "total_minutes": 200,
        "rating": 87,
        "publish_year": 1999,
        "goals": ["relationship", "emotion", "career"],
        "moods": ["温和", "实用", "疗愈"],
    },
    {
        "title": "月亮与六便士",
        "author": "威廉·萨默塞特·毛姆",
        "category": "文学",
        "tags": ["小说", "理想", "选择", "艺术", "英国文学"],
        "intro": (
            "证券经纪人抛下体面生活去追逐绘画，像被某种力量附身。"
            "毛姆不评判他的自私，只把'理想与现实'的撕扯摊在你面前。"
            "适合正在纠结要不要换一条路的人。"
        ),
        "difficulty": 3,
        "total_minutes": 300,
        "rating": 89,
        "publish_year": 1919,
        "goals": ["emotion", "growth", "career"],
        "moods": ["激荡", "孤独", "热烈"],
    },
    {
        "title": "活着为了讲述",
        "author": "加西亚·马尔克斯",
        "category": "传记",
        "tags": ["回忆录", "写作", "拉美", "成长", "文学"],
        "intro": (
            "马尔克斯的晚年回忆录：从哥伦比亚小镇的童年，到记者生涯与《百年孤独》的诞生。"
            "生活不是我们活过的日子，而是我们记住的日子。适合爱写作、爱故事的人。"
        ),
        "difficulty": 3,
        "total_minutes": 480,
        "rating": 88,
        "publish_year": 2002,
        "goals": ["knowledge", "emotion", "growth"],
        "moods": ["浪漫", "厚重", "炽热"],
    },
    {
        "title": "苏菲的世界",
        "author": "乔斯坦·贾德",
        "category": "哲学",
        "tags": ["哲学入门", "思想史", "小说", "启蒙", "通俗"],
        "intro": (
            "通过少女苏菲收到的神秘信件，把从苏格拉底到萨特的两千五百年哲学史讲成悬疑故事。"
            "是哲学零基础读者最友好的入口。适合想系统入门、又怕枯燥的人。"
        ),
        "difficulty": 3,
        "total_minutes": 540,
        "rating": 87,
        "publish_year": 1991,
        "goals": ["knowledge", "growth", "emotion"],
        "moods": ["好奇", "轻松", "启蒙"],
    },
    {
        "title": "红楼梦",
        "author": "曹雪芹",
        "category": "古典",
        "tags": ["古典小说", "家族", "命运", "人物群像", "中国文学"],
        "intro": (
            "荣宁二府的兴衰与一群女子的命运沉浮。它既是爱情悲剧，也是社会与人生的百科全书。"
            "建议配合导读逐回阅读，不必一次读完。适合愿意慢读、反复回味的读者。"
        ),
        "difficulty": 5,
        "total_minutes": 1800,
        "rating": 97,
        "publish_year": 1791,
        "goals": ["knowledge", "emotion", "growth"],
        "moods": ["悲悯", "细腻", "宏大"],
    },
]
