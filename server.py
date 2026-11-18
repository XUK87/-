#!/usr/bin/env python3
"""
黑手党之夜 · 狼人杀在线法官（10人局）

房主运行：
    python3 server.py              # 默认端口 8000
    python3 server.py --port 9000
然后把链接发给其他玩家（局域网用 http://<你的IP>:8000，
异地联机可用 cloudflared / ngrok 把 8000 端口映射到公网）。

每位玩家用自己的浏览器打开链接、输入昵称加入；满 10 人后由房主开始。
程序担任法官：随机发身份、夜晚依次叫身份行动、白天主持发言与投票、判定胜负。
所有身份与夜间信息只发送给对应玩家本人。
"""
import argparse
import json
import os
import random
import secrets
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# ───────────────────────── 角色 ─────────────────────────
LUCIANO, SONNY, CHICHI = "Luciano", "Sonny Boy", "Chi Chi"
STEVIE, FLORENCE, NATALIA, BOTTI = "Stevie", "Florence", "Natalia", "Botti"
GAMBINO, MAN, WOMAN = "Gambino", "男人", "女人"

ROLES = [LUCIANO, SONNY, CHICHI, STEVIE, FLORENCE, NATALIA, BOTTI, GAMBINO, MAN, WOMAN]
MAFIA_ROLES = {LUCIANO, SONNY, CHICHI}
GOD_ROLES = {STEVIE, FLORENCE, NATALIA, BOTTI}
VILLAGER_ROLES = {GAMBINO, MAN, WOMAN}
N_PLAYERS = 10

ROLE_FACTION = {
    LUCIANO: "Bocchetti家族 · 黑手党", SONNY: "Bocchetti家族 · 黑手党", CHICHI: "Bocchetti家族 · 黑手党",
    STEVIE: "这是我的选择 · 神", FLORENCE: "这是我的选择 · 神", NATALIA: "这是我的选择 · 神",
    BOTTI: "这是我的选择 · 神", GAMBINO: "依然会被命运绑架 · 民", MAN: "依然会被命运绑架 · 民",
    WOMAN: "依然会被命运绑架 · 民",
}

ROLE_DESC = {
    LUCIANO: [
        "第一代教父：可以在白天放逐公投前自爆，并选择带走任意一名玩家，同时立刻进入黑夜",
        "非自爆出局不得发动技能；只能在全部儿子（Sonny Boy、Chi Chi）在场时自爆",
        "偏心的父亲：家族阵营内默认无条件支持 Sonny Boy 的选择",
    ],
    SONNY: [
        "挚爱之人：如果第一晚所选择开枪对象是 Florence，则整个家族此次攻击无效",
        "隐居烤披萨的老大：第一晚如被查验将显示身份为好人",
        "家族只能靠他：若场上家族只剩兄弟两人，产生分歧时 Sonny Boy 拥有绝对话语权",
    ],
    CHICHI: [
        "超级大反派：被放逐公投出局后进入“假死”状态，当天夜里仍拥有开枪权利；“假死”维持一夜，天亮后自动暴露真实身份并出局（不能抵抗毒药和子弹）",
        "给你一个无法拒绝的建议：暴露身份出局时，可指定场上任意一名玩家被禁言，仅能使用手语，持续一整轮公投发言（包括遗言）",
        "黄玫瑰过敏：如果开枪对象被 Stevie 守护，则第二天夜晚 Chi Chi 丧失攻击能力",
    ],
    STEVIE: [
        "让我替你守护他：每晚可以暗中选择一名玩家守护，该玩家当晚不被黑手党杀害（不能免疫毒药）；不能连续两晚守护同一人；可以守护自己",
        "天使姐姐：可以持续默默守护 Florence；但不能在公开发言中以此作为“金水”发言，一旦说出则 Stevie 本人当场出局；也可选择用自己的出局保住 Florence",
        "大作家：如对本轮投票有异议，可在投票结束后、遗言开始前发动，改变投票结果并另选一人驱逐；只能发动一次，发动后立刻丧失守护能力",
    ],
    FLORENCE: [
        "终有一天你会明白：每晚可以选择一名玩家窥视其真实身份（好人/坏人）",
        "回伦敦吧：当晚一切攻击对自己无效，同时当晚失去查验技能；只能发动一次",
        "第一晚所有攻击无条件免疫",
    ],
    NATALIA: [
        "战火也被冰冻：拥有两瓶药水，解药可以救活一名当晚被黑手党杀害的玩家，毒药可以毒杀一名玩家",
        "每晚只可选择使用一瓶药水",
        "西西里最美强女人：第一晚所有攻击无条件免疫",
    ],
    BOTTI: [
        "罗密欧与朱丽叶：第一晚首位睁眼，选择两名玩家绑定（可以选自己）；若其中有一人被放逐或死亡，另一人会为之殉情",
        "若两人分属不同阵营，则与 Botti 一起形成第三方阵营，除掉其他所有玩家后方可获胜",
        "Gold or Stone：若绑定自己和 Sonny Boy，则二人获得一次“换箱子”机会，夜间受到黑手党伤害自动反弹给 Chi Chi（只触发一次）",
        "God bless my children：Botti 和 Sonny Boy 绑定时，Natalia 的毒药对二人无效",
        "从小只喜欢Sonny Boy：若绑定自己和 Chi Chi，法官以手势告知链接失败，且双方互相暴露身份",
        "Bocchetti Family：若绑定的两人均属于黑手党，则 Botti 立即加入黑手党回归家族",
        "新泽西女子拳王：第一晚所有攻击无条件免疫",
    ],
    GAMBINO: [
        "新的罗马帝国：Luciano 自爆出局直接进入黑夜后，Gambino 当晚拥有一次重新选择阵营的权利，选择黑手党家族阵营则在后一晚生效",
        "血债血偿：仅当 Gambino 选择好人阵营时可触发，天黑时可查验一个人，询问法官是否为 Chi Chi；只有一次机会",
        "注意：当 Gambino 被 Botti 绑定演绎《罗密欧与朱丽叶》时，以上所有特殊 buff 失效",
    ],
    MAN: [
        "请祝福我们吧：男人和女人夜间无法确认彼此身份",
        "你是爱的奇迹：男人和女人共同活到最后且好人阵营获胜，则两名玩家共同获得特殊奖励",
    ],
    WOMAN: [
        "请祝福我们吧：男人和女人夜间无法确认彼此身份",
        "你是爱的奇迹：男人和女人共同活到最后且好人阵营获胜，则两名玩家共同获得特殊奖励",
    ],
}

CAMP_NAME = {"good": "好人阵营", "mafia": "黑手党阵营", "third": "第三方阵营"}

# 各环节时限（秒）
NIGHT_T, LOVERS_T, SPEECH_T, LAST_T = 60, 30, 180, 120
VOTE_T, WINDOW_T, MUTE_T, EXPLODE_T = 60, 10, 40, 30


def spec(title, text, options, count=1, changeable=False):
    """options: [(value, label), ...]"""
    return {"title": title, "text": text, "options": [list(o) for o in options],
            "count": count, "changeable": changeable}


def ack(title, text, button="确认"):
    return spec(title, text, [("ok", button)])


class Player:
    def __init__(self, num, name, role):
        self.num, self.name, self.role = num, name, role
        self.status = "alive"  # alive / fake（Chi Chi假死）/ dead

    @property
    def alive(self):
        return self.status in ("alive", "fake")

    @property
    def on_table(self):
        return self.status == "alive"


class GameOver(Exception):
    pass


# ───────────────────────── 游戏逻辑（法官线程） ─────────────────────────
class Game:
    def __init__(self, room, mode):
        self.room, self.cond, self.mode, self.fast = room, room.cond, mode, room.fast
        roles = ROLES[:]
        random.shuffle(roles)
        self.seats = [dict(x) for x in room.seats]
        self.P = {s["num"]: Player(s["num"], s["name"], roles[i]) for i, s in enumerate(self.seats)}
        self.role_map = {p.role: p for p in self.P.values()}

        self.ann, self.log = [], []
        self.privates = {n: [] for n in self.P}
        self.requests, self.responses, self.bot_due = {}, {}, {}
        self.deadline = None
        self.phase_title, self.phase_info = "游戏即将开始", ""
        self.speaker = None
        self.night = self.day = 0
        self.stage = "night"
        self.interrupt = None
        self.in_discussion = False
        self.team = None
        self.ended, self.result = False, None
        self.aborted = False

        self.lovers = None
        self.link_pair = None
        self.link_failed = self.third = self.botti_joined = False
        self.goldstone = self.goldstone_used = self.godbless = False
        self.gambino_bound = False
        self.gambino_camp = self.gambino_pending = None
        self.gambino_checked = self.gambino_choose_tonight = self.luciano_exploded = False
        self.stevie_last = None
        self.stevie_writer_used = self.florence_london_used = False
        self.antidote = self.poison = True
        self.chichi_allergy_next = False
        self.mute = None

    # ───── 基础工具 ─────
    def is_bot(self, num):
        return self.seats[num - 1]["bot"]

    def by_role(self, r):
        return self.role_map[r]

    def living(self):
        return [p for p in self.P.values() if p.alive]

    def table(self):
        return [p for p in self.P.values() if p.on_table]

    def label(self, p):
        return f"{p.num}号 {p.name}"

    def desc(self, p):
        return f"{p.num}号{p.name}（{p.role}）"

    def note(self, text):
        tag = f"第{self.night}夜" if self.stage == "night" else f"第{self.day}天"
        self.log.append(f"[{tag}] {text}")

    def private(self, num, text):
        with self.cond:
            self.privates[num].append({"t": f"第{self.night}夜" if self.stage == "night" else f"第{self.day}天",
                                       "text": text})
            self.cond.notify_all()

    def announce(self, text):
        if self.aborted:
            raise GameOver()
        with self.cond:
            self.ann.append({"id": len(self.ann), "text": text})
            self.cond.notify_all()
        if not self.fast:
            time.sleep(min(1.2 + len(text) * 0.24, 9))

    def set_phase(self, title, info=""):
        with self.cond:
            self.phase_title, self.phase_info = title, info
            self.cond.notify_all()

    def idle(self):
        time.sleep(0.2 if self.fast else random.uniform(5, 9))

    def request(self, specs, timeout, wait_all=True, interruptible=False):
        """向若干玩家发出操作请求，阻塞到全部回应 / 超时 / 被打断。返回 {num: value或None}"""
        if not specs:
            return {}
        if self.fast and not wait_all:
            timeout = 1
        with self.cond:
            self.responses = {}
            for num, sp in specs.items():
                self.requests[num] = dict(sp, id=secrets.token_hex(4))
                if self.is_bot(num):
                    self.bot_due[num] = time.time() + random.uniform(0.2, 0.8 if self.fast else 2.5)
            self.deadline = time.time() + timeout
            self.cond.notify_all()
            while True:
                now = time.time()
                for num, due in list(self.bot_due.items()):
                    if now >= due:
                        self.responses[num] = self.bot_answer(self.requests[num])
                        del self.bot_due[num]
                if wait_all and all(n in self.responses for n in specs):
                    break
                if self.aborted:
                    self.requests, self.bot_due, self.deadline = {}, {}, None
                    raise GameOver()
                if now >= self.deadline or (interruptible and self.interrupt):
                    break
                self.cond.wait(0.3)
            res = {n: self.responses.get(n) for n in specs}
            self.requests, self.bot_due, self.deadline = {}, {}, None
            self.cond.notify_all()
            return res

    @staticmethod
    def bot_answer(sp):
        vals = [o[0] for o in sp["options"]]
        if sp["count"] > 1:
            return random.sample(vals, sp["count"])
        return random.choice(vals)

    def choose_player(self, num, title, text, cands, timeout=NIGHT_T, none_label=None):
        opts = [(str(p.num), self.label(p)) for p in cands]
        if none_label:
            opts.append(("0", none_label))
        v = self.request({num: spec(title, text, opts)}, timeout)[num]
        return self.P[int(v)] if v and v != "0" else None

    # ───── 阵营 ─────
    def base_bad(self, p):
        return (p.role in MAFIA_ROLES or (p.role == BOTTI and self.botti_joined)
                or (p.role == GAMBINO and self.gambino_camp == "mafia"))

    def third_members(self):
        return (set(self.lovers) | {self.by_role(BOTTI).num}) if self.third else set()

    def camp(self, p):
        if p.num in self.third_members():
            return "third"
        return "mafia" if self.base_bad(p) else "good"

    # ───── 死亡 ─────
    def kill(self, p, cause, deaths):
        if p.status == "dead":
            return
        p.status = "dead"
        deaths.append(p)
        self.note(f"{self.desc(p)} 出局（{cause}）")
        if self.lovers and p.num in self.lovers:
            other = self.P[self.lovers[0] if self.lovers[1] == p.num else self.lovers[1]]
            if other.alive:
                self.kill(other, "为恋人殉情", deaths)

    def also_out(self, deaths, main):
        rest = [d for d in deaths if d is not main]
        return ("同时" + "、".join(f"{d.num}号" for d in rest) + "出局。") if rest else ""

    # ───── 主流程 ─────
    def run(self):
        try:
            self.play()
        except GameOver:
            pass
        except Exception:
            traceback.print_exc()
            self.announce("法官程序出现错误，游戏中止。")
            with self.cond:
                self.ended = True
                self.result = {"winner": "游戏异常中止", "roles": self.roles_table(), "log": self.log,
                               "lovers": self.lovers, "achievement": None}

    def play(self):
        self.set_phase("发身份", "请查看你的身份卡")
        self.announce("游戏开始，身份已随机发放，请查看自己的身份。")
        time.sleep(0.5 if self.fast else 8)
        while True:
            deaths, exposed = self.run_night()
            self.dawn(deaths, exposed)
            self.run_day()

    # ───── 夜晚 ─────
    def role_phase(self, name, opening, active, action, closing=None):
        self.set_phase(f"第{self.night}夜 · {name}", "天黑请闭眼，等待法官叫到你的身份")
        self.announce(opening)
        if active:
            action()
        else:
            self.idle()
        self.announce(closing or f"{name}请闭眼。")

    def run_night(self):
        self.night += 1
        self.stage = "night"
        if self.gambino_pending == "mafia":
            self.gambino_camp, self.gambino_pending = "mafia", None
            self.note("Gambino 加入黑手党阵营正式生效")
            self.private(self.by_role(GAMBINO).num, "你加入黑手党家族阵营已生效，今晚起与黑手党一同睁眼。")
        self.chichi_allergy_now, self.chichi_allergy_next = self.chichi_allergy_next, False
        self.fake_chichi_tonight = self.by_role(CHICHI).status == "fake"
        self.guard = self.kill_target = self.poison_target = None
        self.florence_london_tonight = self.save_used_tonight = False

        self.set_phase(f"第{self.night}夜", "天黑请闭眼")
        self.announce(f"天黑请闭眼。第{self.night}夜。")

        if self.night == 1:
            self.role_phase("Botti", "Botti请睁眼，选择两名玩家共同演绎《罗密欧与朱丽叶》。", True, self.botti_action)
            self.role_phase("罗密欧与朱丽叶", "被Botti选中的两位玩家，请睁眼，确认彼此身份，判断彼此阵营。",
                            bool(self.lovers or self.link_failed), self.lovers_action, "罗密欧与朱丽叶请闭眼。")
        self.role_phase("Stevie", "Stevie请睁眼，选择你要守护的对象。",
                        self.by_role(STEVIE).on_table and not self.stevie_writer_used, self.stevie_action)
        shooters = self.mafia_shooters()
        self.role_phase("黑手党", "黑手党请睁眼" + ("，确认彼此身份" if self.night == 1 else "") + "，选择开枪对象。",
                        bool(shooters), lambda: self.mafia_action(shooters))
        self.role_phase("Florence", "Florence请睁眼，选择你要查验的对象。",
                        self.by_role(FLORENCE).on_table, self.florence_action)
        self.role_phase("Natalia", "Natalia请睁眼。", self.by_role(NATALIA).on_table, self.natalia_action)
        if self.luciano_exploded:
            gam = self.by_role(GAMBINO)
            active = gam.on_table and not self.gambino_bound and (
                self.gambino_choose_tonight or (self.gambino_camp == "good" and not self.gambino_checked))
            self.role_phase("Gambino", "Gambino请睁眼。", active, self.gambino_action)
            self.gambino_choose_tonight = False
        return self.resolve_night()

    def mafia_members(self):
        return [p for p in self.living() if self.base_bad(p)]

    def mafia_shooters(self):
        return [p for p in self.mafia_members() if not (p.role == CHICHI and self.chichi_allergy_now)]

    def botti_action(self):
        botti = self.by_role(BOTTI)
        opts = [(str(p.num), self.label(p)) for p in self.living()]
        v = self.request({botti.num: spec("罗密欧与朱丽叶", "请选择两名玩家绑定（可以选自己），选好后点确认。",
                                          opts, count=2)}, NIGHT_T)[botti.num]
        if not v:
            self.private(botti.num, "你未在时限内完成选择，本局无人被绑定。")
            self.note("Botti 超时未绑定")
            return
        a, b = self.P[int(v[0])], self.P[int(v[1])]
        roles = {a.role, b.role}
        self.note(f"Botti 绑定 {self.desc(a)} 与 {self.desc(b)}")
        if roles == {BOTTI, CHICHI}:
            self.link_failed, self.link_pair = True, (a, b)
            self.note("Botti 绑定自己与 Chi Chi → 链接失败，双方互相暴露身份")
            self.private(botti.num, f"👎 法官手势：链接失败！你与对方将互相暴露身份。你当前所属阵营：{CAMP_NAME['good']}")
            return
        self.lovers = (a.num, b.num)
        if GAMBINO in roles:
            self.gambino_bound = True
            self.note("Gambino 被绑定，其所有特殊 buff 失效")
        if roles == {BOTTI, SONNY}:
            self.goldstone = self.godbless = True
            self.note("触发 Gold or Stone 与 God bless my children")
        if a.role in MAFIA_ROLES and b.role in MAFIA_ROLES:
            self.botti_joined = True
            self.note("绑定两人均为黑手党 → Botti 立即加入黑手党")
        elif (a.role in MAFIA_ROLES) != (b.role in MAFIA_ROLES):
            self.third = True
            self.note("恋人分属不同阵营 → 与 Botti 组成第三方阵营")
        msg = f"👍 绑定成功：{a.num}号 与 {b.num}号。此时你（Botti）所属阵营是：【{CAMP_NAME[self.camp(botti)]}】"
        if self.botti_joined:
            msg += "。Bocchetti Family：你立即加入黑手党，今晚起与黑手党一同睁眼。"
        self.private(botti.num, msg)

    def lovers_action(self):
        if self.link_failed:
            a, b = self.link_pair
            text = f"👎 链接失败！你们二人互相暴露身份：{self.desc(a)}、{self.desc(b)}"
        else:
            a, b = (self.P[n] for n in self.lovers)
            text = f"你们是罗密欧与朱丽叶：{self.desc(a)}、{self.desc(b)}。若其中一人被放逐或死亡，另一人会为之殉情。"
            if self.third:
                text += " ⚠ 你们分属不同阵营，与 Botti 一起组成【第三方阵营】，需除掉其他所有玩家方可获胜。"
            else:
                text += f" 你们同属【{CAMP_NAME[self.camp(a)]}】。"
        for p in (a, b):
            self.private(p.num, text)
        self.request({p.num: ack("罗密欧与朱丽叶", text) for p in (a, b)}, LOVERS_T)

    def stevie_action(self):
        stevie, flo = self.by_role(STEVIE), self.by_role(FLORENCE)
        cands = [p for p in self.living() if p.num != self.stevie_last or p is flo]
        text = f"天使姐姐：Florence 是 {flo.num}号，可以持续守护她。"
        if self.stevie_last:
            text = f"上一晚你守护了 {self.stevie_last}号（不能连续守护同一人，Florence 除外）。" + text
        g = self.choose_player(stevie.num, "选择守护对象", text, cands, none_label="空守")
        self.guard = g
        self.stevie_last = g.num if g else None
        self.note(f"Stevie 守护 {self.desc(g) if g else '空守'}")
        self.private(stevie.num, f"今晚你守护了：{f'{g.num}号' if g else '无人'}")

    def mafia_action(self, shooters):
        members = self.mafia_members()
        sonny = self.by_role(SONNY)
        lines = []
        for p in members:
            tag = "（假死，今晚仍可开枪）" if p.status == "fake" else ""
            if p.role == CHICHI and self.chichi_allergy_now:
                tag += "（黄玫瑰过敏，今晚无法开枪）"
            lines.append(f"{self.desc(p)}{tag}")
        text = "家族成员：" + "；".join(lines) + "。"
        if sonny in shooters:
            text += " 意见分歧时以 Sonny Boy 的选择为准。所有人提交后结束，提交前可以改选。"
        opts = [(str(p.num), self.label(p)) for p in self.living()] + [("0", "空枪")]
        with self.cond:
            self.team = {"members": [p.num for p in members], "shooters": [p.num for p in shooters],
                         "chat": [], "info": text}
        res = self.request({p.num: spec("选择开枪对象", text, opts, changeable=True) for p in shooters}, NIGHT_T)
        with self.cond:
            self.team = None
        if sonny in shooters and res.get(sonny.num) is not None:
            v = res[sonny.num]
        else:
            picks = [v for v in res.values() if v is not None]
            if not picks:
                v = "0"
            else:
                cnt = {x: picks.count(x) for x in picks}
                top = max(cnt.values())
                v = random.choice([x for x, c in cnt.items() if c == top])
        t = self.P[int(v)] if v != "0" else None
        self.note(f"黑手党开枪 → {self.desc(t) if t else '空枪'}")
        for p in members:
            self.private(p.num, f"家族最终开枪对象：{f'{t.num}号' if t else '空枪'}")
        if t and self.night == 1 and t.role == FLORENCE and sonny.alive:
            self.note("Sonny Boy【挚爱之人】：第一晚开枪 Florence，家族本次攻击无效")
            t = None
        if t and self.goldstone and not self.goldstone_used and t.role in (SONNY, BOTTI):
            self.goldstone_used = True
            t = self.by_role(CHICHI)
            self.note(f"Gold or Stone 自动触发：伤害反弹给 {self.desc(t)}")
        self.kill_target = t

    def florence_action(self):
        flo = self.by_role(FLORENCE)
        opts = [(str(p.num), self.label(p)) for p in self.living() if p is not flo]
        if not self.florence_london_used:
            opts.append(("L", "🌂 发动【回伦敦吧】（今晚免疫一切攻击，但不能查验，仅一次）"))
        opts.append(("0", "不查验"))
        v = self.request({flo.num: spec("选择查验对象", "请选择一名玩家窥视其真实身份。", opts)}, NIGHT_T)[flo.num]
        if v == "L":
            self.florence_london_used = self.florence_london_tonight = True
            self.note("Florence 发动【回伦敦吧】")
            self.private(flo.num, "你回到了伦敦，今晚一切攻击对你无效。")
            return
        if not v or v == "0":
            self.note("Florence 未查验")
            return
        t = self.P[int(v)]
        bad = self.base_bad(t) and not (self.night == 1 and t.role == SONNY)
        res = "坏人 👎" if bad else "好人 👍"
        self.note(f"Florence 查验 {self.desc(t)} → {res}")
        self.private(flo.num, f"查验结果：{t.num}号 是【{res}】")

    def natalia_action(self):
        nat = self.by_role(NATALIA)
        t = self.kill_target
        text = f"解药：{'有' if self.antidote else '已用'}；毒药：{'有' if self.poison else '已用'}。"
        if self.antidote:
            text += f" 今晚倒牌的是：{f'{t.num}号' if t else '无人（平安夜）'}。"
            self.private(nat.num, f"今晚倒牌的是：{f'{t.num}号' if t else '无人'}")
        else:
            text += " 解药已用，你无法得知今晚倒牌的玩家。"
        text += " 每晚只能使用一瓶药水。"
        opts = []
        if self.antidote and t:
            opts.append(("save", f"💊 使用解药救 {t.num}号"))
        if self.poison:
            opts.append(("poison", "☠ 使用毒药"))
        opts.append(("none", "不使用药水"))
        v = self.request({nat.num: spec("使用药水", text, opts)}, NIGHT_T)[nat.num]
        if v == "save":
            self.antidote, self.save_used_tonight = False, True
            self.note(f"Natalia 使用解药救 {self.desc(t)}")
            self.private(nat.num, f"你使用解药救了 {t.num}号")
        elif v == "poison":
            p = self.choose_player(nat.num, "选择毒杀对象", "请选择要毒杀的玩家。", self.living(), none_label="取消")
            if p:
                self.poison, self.poison_target = False, p
                self.note(f"Natalia 毒杀 {self.desc(p)}")
                self.private(nat.num, f"你毒杀了 {p.num}号")

    def gambino_action(self):
        gam = self.by_role(GAMBINO)
        if self.gambino_choose_tonight and self.gambino_camp is None and self.gambino_pending is None:
            v = self.request({gam.num: spec(
                "新的罗马帝国", "Luciano 已自爆出局。你现在拥有一次重新选择阵营的权利。",
                [("good", "好人阵营（获得【血债血偿】：一次查验某人是否为 Chi Chi）"),
                 ("mafia", "黑手党家族阵营（后一晚生效，届时与黑手党一同睁眼）")])}, NIGHT_T)[gam.num]
            if v == "mafia":
                self.gambino_pending = "mafia"
                self.note("Gambino 选择加入黑手党阵营（后一晚生效）")
                self.private(gam.num, "你选择了黑手党家族阵营，将在下一晚生效。")
                return
            self.gambino_camp = "good"
            self.note("Gambino 选择好人阵营" + ("（超时默认）" if v is None else ""))
            self.private(gam.num, "你属于好人阵营。")
        if self.gambino_camp == "good" and not self.gambino_checked:
            t = self.choose_player(gam.num, "血债血偿", "你可以查验一名玩家，询问法官其是否为 Chi Chi（仅一次）。",
                                   [p for p in self.living() if p is not gam], none_label="暂不使用")
            if t:
                self.gambino_checked = True
                yes = t.role == CHICHI
                self.note(f"Gambino【血债血偿】查验 {self.desc(t)} → {'是' if yes else '不是'} Chi Chi")
                self.private(gam.num, f"【血债血偿】{t.num}号 {'👍 是 Chi Chi' if yes else '👎 不是 Chi Chi'}")

    def immune(self, p, kind):
        if self.night == 1 and p.role in (FLORENCE, NATALIA, BOTTI):
            return True
        if self.florence_london_tonight and p.role == FLORENCE:
            return True
        return kind == "poison" and self.godbless and p.role in (BOTTI, SONNY)

    def resolve_night(self):
        deaths = []
        t = self.kill_target
        if t:
            if self.guard is t:
                self.chichi_allergy_next = True
                self.note(f"{self.desc(t)} 被 Stevie 守护免于被杀 → 下一晚 Chi Chi 黄玫瑰过敏")
            elif self.immune(t, "kill"):
                self.note(f"{self.desc(t)} 免疫本次攻击")
            elif not self.save_used_tonight:
                self.kill(t, "被黑手党开枪", deaths)
        p = self.poison_target
        if p:
            if self.immune(p, "poison"):
                self.note(f"{self.desc(p)} 免疫毒药")
            else:
                self.kill(p, "被毒杀", deaths)
        exposed = False
        if self.fake_chichi_tonight:
            exposed = True
            self.kill(self.by_role(CHICHI), "假死结束，暴露身份", deaths)
        return deaths, exposed

    # ───── 白天 ─────
    def check_end(self):
        w = self.winner()
        if w:
            self.finish(w)
            raise GameOver()

    def winner(self):
        alive = self.living()
        if not alive:
            return "draw"
        m = [p for p in alive if self.camp(p) == "mafia"]
        g = [p for p in alive if self.camp(p) == "good"]
        t = [p for p in alive if self.camp(p) == "third"]
        if self.third and t and not m and not g:
            return "third"
        if not m and not t:
            return "good"
        if not t:
            gods = [p for p in g if p.role in GOD_ROLES]
            vill = [p for p in g if p.role in VILLAGER_ROLES]
            if self.mode == "edge" and (not gods or not vill):
                return "mafia"
            if self.mode == "city" and not g:
                return "mafia"
        return None

    def muted(self, p):
        return bool(self.mute and self.mute[0] == p.num and self.mute[1] == self.day)

    def speech(self, p, kind, interruptible=False):
        tail = "（已被禁言，只能使用手语）" if self.muted(p) else ""
        with self.cond:
            self.speaker = p.num
        self.set_phase(f"第{self.day}天 · {kind}", f"{p.num}号 {p.name} {kind}中{tail}")
        self.announce(f"请{p.num}号{'发表遗言' if kind == '遗言' else '发言'}。{tail}")
        self.request({p.num: ack(kind, f"轮到你{kind}了。{tail}说完后点击下方按钮。", f"{kind}结束")},
                     LAST_T if kind == "遗言" else SPEECH_T, interruptible=interruptible)
        with self.cond:
            self.speaker = None

    def dawn(self, deaths, exposed):
        self.day += 1
        self.stage = "day"
        self.set_phase(f"第{self.day}天 · 天亮了")
        self.announce("天亮了，请大家睁眼。")
        chichi = self.by_role(CHICHI)
        others = sorted([d for d in deaths if not (exposed and d is chichi)], key=lambda x: x.num)
        if others:
            self.announce("昨晚倒牌的是：" + "、".join(f"{d.num}号" for d in others) + "。")
        else:
            self.announce("昨晚是平安夜。")
        if exposed:
            self.announce(f"{chichi.num}号的假死状态结束，暴露真实身份：Chi Chi，出局。")
            self.announce(f"Chi Chi发动技能：给你一个无法拒绝的建议。请{chichi.num}号指定一名玩家禁言。")
            self.set_phase(f"第{self.day}天 · Chi Chi 禁言", f"等待 {chichi.num}号 选择禁言对象")
            m = self.choose_player(chichi.num, "给你一个无法拒绝的建议",
                                   "指定一名玩家禁言：本轮公投发言（包括遗言）只能使用手语。",
                                   self.table(), MUTE_T, none_label="不禁言")
            if m:
                self.mute = (m.num, self.day)
                self.note(f"Chi Chi 禁言 {self.desc(m)}")
                self.announce(f"{m.num}号被禁言，本轮公投发言包括遗言只能使用手语。")
        self.check_end()
        for d in others:
            self.speech(d, "遗言")

    def run_day(self):
        tbl = self.table()
        start = random.choice(tbl)
        direction = random.choice(["顺序", "逆序"])
        nums = sorted(p.num for p in tbl)
        if direction == "逆序":
            nums.reverse()
        i = nums.index(start.num)
        order = [self.P[n] for n in nums[i:] + nums[:i]]
        self.announce(f"现在开始发言，从{start.num}号开始，{direction}发言。")
        idx = 0
        while idx < len(order):
            p = order[idx]
            if not p.on_table:
                idx += 1
                continue
            with self.cond:
                self.in_discussion, self.interrupt = True, None
            self.speech(p, "发言", interruptible=True)
            with self.cond:
                self.in_discussion = False
                intr, self.interrupt = self.interrupt, None
            if intr:
                kind, arg = intr
                if kind == "explode":
                    self.do_explode(self.P[arg])
                    return
                if kind == "report":
                    self.do_report(*arg)
                continue
            idx += 1
        self.vote()

    def do_explode(self, lu):
        self.set_phase(f"第{self.day}天 · 自爆", f"{lu.num}号 自爆")
        self.announce(f"{lu.num}号自爆，请选择带走一名玩家。")
        t = self.choose_player(lu.num, "第一代教父", "你已自爆，请选择带走一名玩家。",
                               [x for x in self.table() if x is not lu], EXPLODE_T)
        if t is None:
            t = random.choice([x for x in self.table() if x is not lu])
            self.note("Luciano 超时未选择，随机带走")
        deaths = []
        self.luciano_exploded = self.gambino_choose_tonight = True
        self.kill(lu, "Luciano 自爆", deaths)
        self.kill(t, "被 Luciano 自爆带走", deaths)
        self.announce(f"{lu.num}号自爆出局，带走了{t.num}号。" + self.also_out(deaths, t) + "立刻进入黑夜。")
        self.check_end()

    def do_report(self, reporter, target):
        """任何在场玩家都可举报，全体在场玩家投票，超过半数同意才判罚（无人拥有上帝视角）"""
        r, t = self.P[reporter], self.P[target]
        self.set_phase(f"第{self.day}天 · 举报表决", f"{r.num}号 举报 {t.num}号 以守护Florence作为金水发言")
        self.announce(f"{r.num}号举报{t.num}号以守护Florence作为金水发言，请全体在场玩家表决是否判罚。")
        voters = self.table()
        res = self.request({p.num: spec("举报表决", f"{r.num}号 举报 {t.num}号 在公开发言中以“守护 Florence”作为金水。是否判罚？",
                                        [("yes", "同意判罚"), ("no", "不同意")]) for p in voters}, VOTE_T)
        yes = sorted(n for n, v in res.items() if v == "yes")
        self.note(f"{r.num}号举报{t.num}号，同意：{yes}")
        if len(yes) * 2 > len(voters):
            self.announce(f"表决通过（{len(yes)}/{len(voters)}票同意）。")
            self.do_penalty(t)
        else:
            self.announce(f"表决未通过（{len(yes)}/{len(voters)}票同意），不予判罚。")

    def do_penalty(self, p):
        if p.role == STEVIE and p.on_table:
            deaths = []
            self.kill(p, "违反【天使姐姐】规则当场出局", deaths)
            self.announce(f"法官判罚：{p.num}号违反规则，当场出局。" + self.also_out(deaths, p))
            self.check_end()
        else:
            self.announce(f"法官判罚：对{p.num}号的判罚无效。")

    def vote(self):
        self.set_phase(f"第{self.day}天 · 放逐公投", "请投票")
        self.announce("发言结束，现在开始放逐公投，请大家投票。")
        voters = self.table()
        opts = [(str(p.num), self.label(p)) for p in voters] + [("0", "弃票")]
        res = self.request({p.num: spec("放逐公投", "请选择你要放逐的玩家。", opts) for p in voters}, VOTE_T)
        tally = {}
        for v, choice in res.items():
            if choice and choice != "0":
                tally.setdefault(int(choice), []).append(v)
        lines = [f"{t}号 {len(vs)}票（{'、'.join(map(str, sorted(vs)))}号投）"
                 for t, vs in sorted(tally.items(), key=lambda kv: -len(kv[1]))]
        abst = sorted(v for v, c in res.items() if not c or c == "0")
        if abst:
            lines.append("弃票：" + "、".join(map(str, abst)) + "号")
        self.note("投票：" + "；".join(lines))
        top = max((len(v) for v in tally.values()), default=0)
        leaders = [t for t, vs in tally.items() if len(vs) == top]
        ex = self.P[leaders[0]] if top and len(leaders) == 1 else None
        self.announce("投票结果：" + "；".join(lines) + "。" +
                      (f"{ex.num}号得票最多。" if ex else "平票，无人被放逐。"))

        # 技能窗口：所有在场玩家看到相同的窗口，固定时长，避免暴露 Stevie
        stevie = self.by_role(STEVIE)
        self.set_phase(f"第{self.day}天 · 技能窗口", "投票结束、遗言开始前，可发动技能")
        self.announce("投票结束，遗言开始前，如有技能请发动。")
        wspecs = {}
        for p in self.table():
            o = [("none", "不发动")]
            if p is stevie:
                if not self.stevie_writer_used:
                    o.append(("writer", "✒ 发动【大作家】：改变投票结果，另选一人驱逐"))
                if ex and ex.role == FLORENCE:
                    o.append(("angel", f"👼 【天使姐姐】用自己的出局保住 {ex.num}号"))
            wspecs[p.num] = spec("技能窗口", "如需发动技能，请在倒计时内选择。", o, changeable=True)
        w = self.request(wspecs, WINDOW_T, wait_all=False).get(stevie.num)
        if w == "writer":
            self.stevie_writer_used = True
            self.announce(f"{stevie.num}号发动技能【大作家】，改变本次投票结果。")
            new = self.choose_player(stevie.num, "大作家", "请选择改为驱逐的玩家。",
                                     [x for x in self.table() if x is not ex], EXPLODE_T)
            if new:
                self.note(f"Stevie【大作家】将放逐对象由 {self.desc(ex) if ex else '无人'} 改为 {self.desc(new)}")
                ex = new
            self.note("Stevie 发动大作家后丧失守护能力")
        elif w == "angel":
            self.note("Stevie【天使姐姐】用自己的出局保住 Florence")
            self.announce(f"{stevie.num}号用自己的出局保住了{ex.num}号。")
            ex = stevie
        if not ex:
            self.announce("本轮无人被放逐。")
            return
        deaths = []
        if ex.role == CHICHI and ex.status == "alive":
            ex.status = "fake"
            self.note(f"{self.desc(ex)} 被放逐 → 进入假死状态")
            self.announce(f"{ex.num}号被放逐出局。")
        else:
            self.kill(ex, "被放逐", deaths)
            self.announce(f"{ex.num}号被放逐出局。" + self.also_out(deaths, ex))
        self.check_end()
        self.speech(ex, "遗言")

    # ───── 结束 ─────
    def roles_table(self):
        return [{"num": p.num, "name": p.name, "role": p.role, "camp": CAMP_NAME[self.camp(p)],
                 "alive": p.alive} for p in self.P.values()]

    def finish(self, w):
        names = {"good": "好人阵营", "mafia": "黑手党阵营", "third": "第三方阵营（罗密欧与朱丽叶）",
                 "draw": "无人（同归于尽）"}
        man, woman = self.by_role(MAN), self.by_role(WOMAN)
        ach = None
        if w == "good" and man.alive and woman.alive:
            ach = f"特殊成就【你是爱的奇迹】：男人{man.num}号与女人{woman.num}号共同活到最后，获得特殊奖励！"
        self.set_phase("游戏结束", f"{names[w]}获胜")
        with self.cond:
            self.ended = True
            self.result = {"winner": f"{names[w]}获胜", "roles": self.roles_table(), "log": self.log,
                           "lovers": list(self.lovers) if self.lovers else None, "achievement": ach}
        self.announce(f"游戏结束，{names[w]}获胜！")
        if ach:
            self.announce(ach)

    # ───── 客户端视图 ─────
    def view(self, num):
        me = self.P[num]
        req = self.requests.get(num)
        if req:
            req = dict(req, answered=self.responses.get(num))
        team = None
        if self.team and num in self.team["members"]:
            picks = {}
            for n in self.team["shooters"]:
                v = self.responses.get(n)
                picks[n] = None if v is None else ("空枪" if v == "0" else f"{v}号")
            team = {"members": [{"num": n, "name": self.P[n].name, "role": self.P[n].role,
                                 "pick": picks.get(n, "无法开枪")} for n in self.team["members"]],
                    "chat": self.team["chat"]}
        sonny, chichi = self.by_role(SONNY), self.by_role(CHICHI)
        can_explode = (self.in_discussion and not self.interrupt and me.role == LUCIANO and me.on_table
                       and sonny.on_table and chichi.on_table)
        return {
            "me": {"num": num, "name": me.name, "role": me.role, "faction": ROLE_FACTION[me.role],
                   "desc": ROLE_DESC[me.role], "out": not me.on_table},
            "seats": [{"num": p.num, "name": p.name, "out": not p.on_table, "speaking": self.speaker == p.num,
                       "muted": self.muted(p), "bot": self.is_bot(p.num)} for p in self.P.values()],
            "phase": {"title": self.phase_title, "info": self.phase_info,
                      "remaining": max(0, round(self.deadline - time.time())) if self.deadline else None},
            "mode": "屠边" if self.mode == "edge" else "屠城",
            "announcements": self.ann,
            "privates": self.privates[num],
            "request": req,
            "team": team,
            "can_explode": can_explode,
            "can_report": self.in_discussion and not self.interrupt and me.on_table,
            "ended": self.ended,
            "result": self.result,
        }


# ───────────────────────── 房间 / HTTP ─────────────────────────
class Room:
    def __init__(self, fast=False):
        self.cond = threading.Condition()
        self.seats = []
        self.game = None
        self.fast = fast

    def seat_of(self, token):
        for s in self.seats:
            if s["token"] == token and token:
                s["seen"] = time.time()
                return s
        return None

    def watchdog(self):
        """大厅：关掉网页的玩家 20 秒后自动让出座位；
        游戏中：所有真人玩家离开 5 分钟后自动解散房间，链接永远不会卡死。"""
        while True:
            time.sleep(5)
            with self.cond:
                now = time.time()
                humans = [s for s in self.seats if not s["bot"]]
                if self.game is None:
                    stale = [s for s in humans if now - s.get("seen", now) > 20]
                    if stale:
                        self.seats = [s for s in self.seats if s not in stale and not s["bot"]]
                        self.renumber()
                elif humans and all(now - s.get("seen", now) > 300 for s in humans) or not humans:
                    self.game.aborted = True
                    self.game = None
                    self.seats = []
                self.cond.notify_all()

    def renumber(self):
        for i, s in enumerate(self.seats):
            s["num"] = i + 1
        if self.seats and not any(s["host"] for s in self.seats):
            self.seats[0]["host"] = True

    def start_game(self, mode):
        self.game = Game(self, mode)
        threading.Thread(target=self.game.run, daemon=True).start()


ROOM = None
INDEX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            with open(INDEX_PATH, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif u.path == "/api/state":
            token = parse_qs(u.query).get("token", [""])[0]
            with ROOM.cond:
                self.send_json(self.state(token))
        else:
            self.send_error(404)

    def state(self, token):
        seat = ROOM.seat_of(token)
        if not seat:
            return {"joined": False, "started": ROOM.game is not None,
                    "count": len(ROOM.seats), "full": len(ROOM.seats) >= N_PLAYERS}
        if ROOM.game is None:
            return {"joined": True, "started": False, "me": {"num": seat["num"], "name": seat["name"]},
                    "host": seat["host"],
                    "seats": [{"num": s["num"], "name": s["name"], "host": s["host"], "bot": s["bot"]}
                              for s in ROOM.seats]}
        v = ROOM.game.view(seat["num"])
        v.update(joined=True, started=True, host=seat["host"])
        return v

    def do_POST(self):
        u = urlparse(self.path)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except Exception:
            return self.send_json({"error": "请求格式错误"}, 400)
        with ROOM.cond:
            try:
                out = self.route(u.path, body)
            except ValueError as e:
                out = {"error": str(e)}
            ROOM.cond.notify_all()
        self.send_json(out)

    def route(self, path, b):
        if path == "/api/join":
            name = str(b.get("name", "")).strip()[:12]
            if not name:
                raise ValueError("请输入昵称")
            if ROOM.game is not None:
                raise ValueError("游戏已经开始")
            if len(ROOM.seats) >= N_PLAYERS:
                raise ValueError("房间已满（10人）")
            if any(s["name"] == name for s in ROOM.seats):
                raise ValueError("昵称已被使用")
            token = secrets.token_urlsafe(16)
            ROOM.seats.append({"num": len(ROOM.seats) + 1, "name": name, "token": token, "seen": time.time(),
                               "host": not any(s["host"] for s in ROOM.seats), "bot": False})
            return {"token": token}

        seat = ROOM.seat_of(b.get("token", ""))
        if not seat:
            raise ValueError("未加入游戏")
        g = ROOM.game

        if path == "/api/start":
            if not seat["host"]:
                raise ValueError("只有房主可以开始游戏")
            if g is not None and not g.ended:
                raise ValueError("游戏进行中")
            if b.get("bots"):
                i = 0
                while len(ROOM.seats) < N_PLAYERS:
                    i += 1
                    ROOM.seats.append({"num": len(ROOM.seats) + 1, "name": f"机器人{i}",
                                       "token": "", "host": False, "bot": True})
            if len(ROOM.seats) != N_PLAYERS:
                raise ValueError(f"需要 {N_PLAYERS} 名玩家，当前 {len(ROOM.seats)} 名")
            ROOM.start_game("city" if b.get("mode") == "city" else "edge")
            return {"ok": True}

        if path == "/api/leave":
            if g is not None:
                raise ValueError("游戏已开始，不能离开座位")
            ROOM.seats.remove(seat)
            ROOM.seats = [s for s in ROOM.seats if not s["bot"]]
            ROOM.renumber()
            return {"ok": True}

        if g is None:
            raise ValueError("游戏尚未开始")
        num = seat["num"]

        if path == "/api/act":
            req = g.requests.get(num)
            if not req or req["id"] != b.get("id"):
                raise ValueError("该操作已失效")
            if num in g.responses and not req["changeable"]:
                raise ValueError("已经提交过了")
            vals = {o[0] for o in req["options"]}
            v = b.get("value")
            if req["count"] > 1:
                if not isinstance(v, list) or len(set(v)) != req["count"] or not set(v) <= vals:
                    raise ValueError(f"请选择 {req['count']} 项")
                v = [str(x) for x in v]
            elif v not in vals:
                raise ValueError("无效选项")
            g.responses[num] = v
            return {"ok": True}

        if path == "/api/chat":
            text = str(b.get("text", "")).strip()[:100]
            if g.team and num in g.team["members"] and text:
                g.team["chat"].append({"num": num, "text": text})
            return {"ok": True}

        if path == "/api/explode":
            if not g.view(num)["can_explode"]:
                raise ValueError("现在不能自爆")
            g.interrupt = ("explode", num)
            return {"ok": True}

        if path == "/api/report":
            if not (g.in_discussion and not g.interrupt and g.P[num].on_table):
                raise ValueError("现在不能举报")
            t = int(b.get("target", 0))
            if t not in g.P or not g.P[t].on_table:
                raise ValueError("无效玩家")
            g.interrupt = ("report", (num, t))
            return {"ok": True}

        if path == "/api/restart":
            if not seat["host"] or not g.ended:
                raise ValueError("只有房主可以在游戏结束后重开")
            ROOM.game = None
            ROOM.seats = [s for s in ROOM.seats if not s["bot"]]
            ROOM.renumber()
            return {"ok": True}

        raise ValueError("未知操作")


def main():
    global ROOM
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    ap.add_argument("--fast", action="store_true", help="测试用：去掉等待与播报间隔")
    args = ap.parse_args()
    ROOM = Room(fast=args.fast)
    threading.Thread(target=ROOM.watchdog, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = "你的IP"
    print("黑手党之夜 · 在线法官已启动")
    print(f"  本机打开：      http://localhost:{args.port}")
    print(f"  同一局域网：    http://{ip}:{args.port}")
    print(f"  异地联机：      cloudflared tunnel --url http://localhost:{args.port}")
    print("按 Ctrl+C 关闭服务器")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已关闭")


if __name__ == "__main__":
    main()
