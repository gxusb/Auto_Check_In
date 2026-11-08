#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
cron "1 12 * * *" script-path=check_in_TianYiYun.py,tag=匹配cron用
new Env('天翼云盘签到')

环境变量：
1. TY_ACCOUNTS：必填，JSON 格式的账号数组。
   多账号示例：
   TY_ACCOUNTS='[
     {"username":"账号1手机号","password":"账号1密码"},
     {"username":"账号2手机号","password":"账号2密码"}
   ]'

2. RANDOM_SIGNIN：可选，是否在签到前随机等待。
   默认：true

3. MAX_RANDOM_DELAY：可选，签到前随机等待的最大秒数。
   默认：120

依赖：
    pip install requests rsa

通知：
    如运行环境提供 notify.py，将统一发送一条汇总通知。
"""

import base64
import io
import json
import math
import os
import random
import re
import time
import unicodedata
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta

import requests
import rsa

# -------------------- 配置 --------------------

REQUEST_TIMEOUT = 15
ACCOUNT_DELAY_RANGE = (5, 20)
RANDOM_SIGNIN = os.getenv("RANDOM_SIGNIN", "true").strip().lower() == "true"

try:
    MAX_RANDOM_DELAY = max(0, int(float(os.getenv("MAX_RANDOM_DELAY", "120"))))
except (TypeError, ValueError):
    MAX_RANDOM_DELAY = 60

try:
    from notify import send as notification_send
except ImportError:
    notification_send = None

# -------------------- 通用函数 --------------------


def display_width(text):
    """计算字符串在终端中的显示宽度，中文及宽字符按两列计算。"""
    width = 0

    for char in str(text):
        if unicodedata.combining(char):
            continue

        width += (
            2
            if unicodedata.east_asian_width(char) in ("W", "F")
            else 1
        )

    return width


def pad_display(text, width):
    """按终端显示宽度补空格，而不是按字符数量补空格。"""
    text = str(text)
    return text + " " * max(0, width - display_width(text))


def log(level, message, account=None):
    """统一日志格式，自动对齐列宽并保留表情图标。"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    account_label = f"账号 {account}" if account is not None else "系统"

    level_text = f"[{level}]"
    account_text = f"[{account_label}]"

    print(
        f"{timestamp} "
        f"{pad_display(level_text, 9)} "
        f"{pad_display(account_text, 8)} "
        f"{message}"
    )


def log_captured_output(output, level="INFO"):
    """将依赖模块直接写入 stdout/stderr 的内容转换为统一日志格式。"""
    for line in output.splitlines():
        if line.strip():
            log(level, line.strip())


def current_time():
    """返回当前本地时间。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def format_duration(seconds):
    """将秒数格式化为易读的中文时长，避免浮点秒数导致格式不稳定。"""
    try:
        total_seconds = max(0, int(math.ceil(float(seconds))))
    except (TypeError, ValueError):
        return "立即执行"

    if total_seconds <= 0:
        return "立即执行"

    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    if hours:
        return f"{hours}小时{minutes}分{secs}秒"
    if minutes:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"


def wait_with_countdown(seconds, task_name):
    """等待指定秒数，并定期输出倒计时。"""
    try:
        total_seconds = max(0, int(math.ceil(float(seconds))))
    except (TypeError, ValueError):
        return

    if total_seconds <= 0:
        return

    log("INFO", f"💤 {task_name}将在 {format_duration(total_seconds)} 后开始")
    deadline = time.monotonic() + total_seconds

    while True:
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            break

        remaining = int(math.ceil(remaining_seconds))
        if remaining <= 10 or remaining % 10 == 0:
            log("INFO", f"⏳ {task_name}倒计时：{format_duration(remaining)}")

        time.sleep(min(1, remaining_seconds))


if notification_send is None:
    log("WARN", "⚠️ 未加载 notify.py，通知内容将输出到控制台")

# -------------------- 天翼云盘签到 --------------------

BASE64_MAP = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
RADIX_MAP = "0123456789abcdefghijklmnopqrstuvwxyz"

LOGIN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 12; Mobile) "
        "AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}

SIGN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 5.1.1; SM-G930K Build/NRD90M; wv) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Version/4.0 Chrome/74.0.3729.136 Mobile Safari/537.36 "
        "Ecloud/8.6.3 Android/22 clientId/355325117317828 "
        "clientModel/SM-G930K imsi/460071114317824 "
        "clientChannelId/qq proVersion/1.0.6"
    ),
    "Referer": (
        "https://m.cloud.189.cn/zhuanti/2016/sign/"
        "index.jsp?albumBackupOpened=1"
    ),
    "Host": "m.cloud.189.cn",
    "Accept-Encoding": "gzip, deflate",
}


class TianYiYunPan:
    def __init__(self, username, password, index):
        self.username = username
        self.password = password
        self.index = index
        self.session = requests.Session()

    @staticmethod
    def _mask_username(username):
        """隐藏用户名的部分信息，避免日志暴露完整账号。"""
        username = str(username)

        if "@" in username:
            name, domain = username.split("@", 1)
            if len(name) <= 1:
                masked_name = name + "*"
            else:
                masked_name = name[0] + "*" * (len(name) - 1)
            return f"{masked_name}@{domain}"

        if username.isdigit() and len(username) == 11:
            return f"{username[:3]}****{username[-4:]}"

        if len(username) <= 1:
            return username + "*"
        return username[0] + "*" * (len(username) - 1)

    @staticmethod
    def _base64_to_radix36(value):
        """将 Base64 字符串转换为接口要求的 36 进制字符串。"""
        output = []
        bit_count = 0
        remainder = 0

        for char in value:
            if char == "=":
                continue

            number = BASE64_MAP.index(char)

            if bit_count == 0:
                bit_count = 1
                output.append(RADIX_MAP[number >> 2])
                remainder = number & 0b11
            elif bit_count == 1:
                bit_count = 2
                output.append(RADIX_MAP[(remainder << 2) | (number >> 4)])
                remainder = number & 0b1111
            elif bit_count == 2:
                bit_count = 3
                output.extend((RADIX_MAP[remainder], RADIX_MAP[number >> 2]))
                remainder = number & 0b11
            else:
                bit_count = 0
                output.extend((
                    RADIX_MAP[(remainder << 2) | (number >> 4)],
                    RADIX_MAP[number & 0b1111],
                ))

        if bit_count == 1:
            output.append(RADIX_MAP[remainder << 2])

        return "".join(output)

    @classmethod
    def _rsa_encode(cls, public_key_value, text):
        """使用登录页提供的 RSA 公钥加密，并转换为接口所需编码。"""
        pem = (
            "-----BEGIN PUBLIC KEY-----\n"
            f"{public_key_value}\n"
            "-----END PUBLIC KEY-----"
        )
        public_key = rsa.PublicKey.load_pkcs1_openssl_pem(pem.encode())
        encrypted = rsa.encrypt(str(text).encode(), public_key)
        encoded = base64.b64encode(encrypted).decode()
        return cls._base64_to_radix36(encoded)

    @staticmethod
    def _add_query_param(url, key, value):
        """仅在参数不存在时，将参数添加到 URL 查询字符串中。"""
        parts = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)

        if any(existing_key == key for existing_key, _ in query):
            return url

        query.append((key, str(value)))
        return urllib.parse.urlunsplit(
            parts._replace(query=urllib.parse.urlencode(query))
        )

    @staticmethod
    def _parse_json_or_jsonp(text):
        """解析 JSON 或 callbackMsg(...) 格式的响应。"""
        text = text.strip()

        if text.startswith("callbackMsg("):
            text = text[len("callbackMsg("):]
            text = text[:-2] if text.endswith(");") else text[:-1]

        return json.loads(text)

    def login(self):
        """登录天翼云盘。"""
        try:
            log("INFO", "👤 开始登录", self.index)
            self.session.headers.update(LOGIN_HEADERS)

            token_url = (
                "https://m.cloud.189.cn/udb/udb_login.jsp"
                "?pageId=1&pageKey=default&clientType=wap"
                "&redirectURL=https://m.cloud.189.cn/zhuanti/2021/"
                "shakeLottery/index.html"
            )
            response = self.session.get(token_url, timeout=REQUEST_TIMEOUT)

            url_match = re.search(r"https?://[^\s'\"]+", response.text)
            if not url_match:
                raise RuntimeError("获取登录 URL 失败")

            auto_url = url_match.group()
            response = self.session.get(
                auto_url,
                headers={**LOGIN_HEADERS, "Referer": token_url},
                timeout=REQUEST_TIMEOUT,
            )

            login_page_url = response.url.replace("/index.html", "/login.html")

            login_page_url = self._add_query_param(
                login_page_url, "protocol", "https"
            )
            login_page_url = self._add_query_param(
                login_page_url, "showback", "true"
            )

            login_page = self.session.get(
                login_page_url,
                headers={**LOGIN_HEADERS, "Referer": auto_url},
                timeout=REQUEST_TIMEOUT,
            )

            key_match = re.search(
                r'id=["\']j_rsaKey["\'][^>]*value=["\']([^"\']+)["\']',
                login_page.text,
            )
            if not key_match:
                raise RuntimeError("登录页未找到 j_rsaKey")

            public_key = key_match.group(1)
            query = urllib.parse.urlsplit(login_page.url).query
            config_url = (
                "https://open.e.189.cn/api/logbox/oauth2/wap/appConf.do?"
                f"{query}"
            )

            config_response = self.session.post(
                config_url,
                headers={**LOGIN_HEADERS, "Referer": login_page.url},
                timeout=REQUEST_TIMEOUT,
            )
            config = config_response.json()

            if str(config.get("result")) != "0":
                raise RuntimeError(
                    f"获取 appConf 失败：{config.get('msg', config)}"
                )

            data = config.get("data") or {}
            param_id = data.get("paramId", "")
            lt = data.get("lt", "")
            req_id = data.get("reqId", "")
            return_url = urllib.parse.quote(data.get("returnUrl", ""), safe="")

            if not all((param_id, lt, req_id, return_url)):
                raise RuntimeError("appConf 关键参数不完整")

            self.session.headers.update({"lt": lt})

            login_name = self.username
            if (
                str(data.get("hasAt", "false")).lower() == "true"
                and "@" not in login_name
            ):
                login_name += "@189.cn"

            params = {
                "apptype": "wap",
                "appKey": data.get("appKey", "cloud"),
                "accountType": data.get("accountType", "02"),
                "dynamicCheck": "false",
                "userName": "{RSA}" + self._rsa_encode(public_key, login_name),
                "epd": "{RSA}" + self._rsa_encode(public_key, self.password),
                "version": "v2.0",
                "returnUrl": return_url,
                "isConfigurable": "true",
                "isOauth2": str(data.get("isOauth2", True)).lower(),
                "state": data.get("state", ""),
                "paramId": param_id,
                "lt": lt,
                "REQID": req_id,
                "callbackMsg": "callbackMsg",
            }

            response = self.session.get(
                "https://open.e.189.cn/api/logbox/oauth2/loginSubmit.do",
                params=params,
                headers={
                    **LOGIN_HEADERS,
                    "Referer": login_page.url,
                    "Accept": "*/*",
                },
                timeout=REQUEST_TIMEOUT,
            )

            try:
                result = response.json()
            except ValueError:
                result = self._parse_json_or_jsonp(response.text)

            if str(result.get("result")) != "0":
                message = result.get("msg") or result.get("message") or result
                raise RuntimeError(f"登录失败：{message}")

            redirect_url = result.get("toUrl")
            if redirect_url:
                self.session.get(
                    redirect_url,
                    headers=LOGIN_HEADERS,
                    timeout=REQUEST_TIMEOUT,
                )

            log(
                "SUCCESS",
                f"✅ 登录成功，用户：{self._mask_username(self.username)}",
                self.index,
            )
            return True

        except Exception as exc:
            log("ERROR", f"❌ 登录失败：{exc}", self.index)
            return False

    def sign_in(self):
        """请求签到接口，并返回签到状态描述。"""
        try:
            log("INFO", "🎯 开始签到", self.index)

            timestamp = round(time.time() * 1000)
            sign_url = (
                "https://api.cloud.189.cn/mkt/userSign.action"
                f"?rand={timestamp}&clientType=TELEANDROID"
                "&version=8.6.3&model=SM-G930K"
            )

            response = self.session.get(
                sign_url,
                headers=SIGN_HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            result = response.json()

            bonus = result.get("netdiskBonus", 0)
            already_signed = str(result.get(
                "isSign", "true")).lower() != "false"

            if already_signed:
                return f"📅 今日已签到，获得 {bonus}M 空间"
            return f"✅ 签到成功，获得 {bonus}M 空间"

        except Exception as exc:
            return f"❌ 签到异常：{exc}"

    def get_size_info(self):
        """获取个人及家庭网盘容量；失败时返回 None。"""
        try:
            response = self.session.get(
                "https://cloud.189.cn/api/portal/getUserSizeInfo.action",
                headers={
                    **LOGIN_HEADERS,
                    "Referer": "https://cloud.189.cn/web/main/",
                    "Accept": "application/json;charset=UTF-8",
                },
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            log("WARN", f"⚠️ 获取网盘容量失败：{exc}", self.index)
            return None

    @staticmethod
    def _format_size(size_bytes):
        """将字节转换为合理的容量单位，保证数值在有效范围内。"""
        try:
            size_value = float(size_bytes)
        except (TypeError, ValueError):
            return "N/A"

        if size_value < 0:
            size_value = 0

        if size_value >= 1024**4:
            return f"{size_value / 1024**4:.4f}TB"
        return f"{size_value / 1024**3:.4f}GB"

    @classmethod
    def _format_capacity_line(cls, label, before_info, after_info, key):
        """展示容量变化、已用容量与总容量，并避免无效容量导致的计算异常。"""
        after = after_info.get(key) if isinstance(after_info, dict) else None
        if not isinstance(after, dict):
            return f"{label}：容量信息无法获取"

        total = after.get("totalSize")
        used = after.get("usedSize")
        if not all(isinstance(value, (int, float)) for value in (total, used)):
            return f"{label}：容量信息无法获取"

        before = before_info.get(key) if isinstance(
            before_info, dict) else None
        before_total = before.get("totalSize") if isinstance(
            before, dict) else None

        change_text = ""
        if isinstance(before_total, (int, float)):
            change_mb = (total - before_total) / 1024**2
            change_text = f"[{change_mb:+.0f}MB] "

        line = (
            f"{label}：{change_text}"
            f"已用 {cls._format_size(max(0, used))} / {cls._format_size(max(0, total))}"
        )

        return line + (
            " ⚠️ 用量数据异常"
            if total < 0 or used < 0 or used > total
            else ""
        )

    @classmethod
    def format_capacity_info(cls, before_info, after_info):
        """整理个人及家庭网盘容量信息。"""
        return "\n".join((
            cls._format_capacity_line(
                "个人", before_info, after_info, "cloudCapacityInfo"
            ),
            cls._format_capacity_line(
                "家庭", before_info, after_info, "familyCapacityInfo"
            ),
        ))

    def run(self):
        """处理当前账号，返回签到及容量结果。"""
        try:
            if self.login():
                before_size_info = self.get_size_info()
                status = self.sign_in()
                after_size_info = self.get_size_info()
                capacity_info = self.format_capacity_info(
                    before_size_info,
                    after_size_info,
                )
            else:
                status = "❌ 登录失败，未完成签到"
                capacity_info = self.format_capacity_info(None, None)

            success = "签到成功" in status or "已签到" in status

        except Exception as exc:
            status = f"❌ 执行异常：{exc}"
            capacity_info = self.format_capacity_info(None, None)
            success = False
        finally:
            self.session.close()

        result = {
            "username": self.username,
            "status": status,
            "capacity": capacity_info,
            "completed_at": current_time(),
            "success": success,
        }

        level = "SUCCESS" if success else "ERROR"
        log(level, f"📊 签到状态：{status}", self.index)
        log("INFO", "📦 网盘容量：", self.index)

        for line in capacity_info.splitlines():
            log("INFO", line, self.index)

        return result

# -------------------- 批量处理与通知 --------------------


def load_accounts():
    """读取并校验 TY_ACCOUNTS 环境变量。"""
    raw_accounts = os.getenv("TY_ACCOUNTS", "").strip()
    if not raw_accounts:
        raise ValueError("未设置 TY_ACCOUNTS 环境变量")

    try:
        accounts = json.loads(raw_accounts)
    except json.JSONDecodeError as exc:
        raise ValueError(f"TY_ACCOUNTS 不是有效的 JSON：{exc}") from exc

    if not isinstance(accounts, list) or not accounts:
        raise ValueError("TY_ACCOUNTS 必须是一个非空 JSON 数组")

    valid_accounts = []
    for index, account in enumerate(accounts, start=1):
        if not isinstance(account, dict):
            raise ValueError(
                f"第 {index} 个账号必须是对象，格式："
                "{'username': '...', 'password': '...'}"
            )

        username = str(account.get("username", "")).strip()
        password = str(account.get("password", ""))

        if not username or not password:
            raise ValueError(f"第 {index} 个账号缺少 username 或 password")

        valid_accounts.append({
            "username": username,
            "password": password,
        })

    return valid_accounts


def process_accounts(accounts):
    """依次处理账号，账号之间增加随机间隔。"""
    results = []

    for index, account in enumerate(accounts, start=1):
        if index > 1:
            delay = random.uniform(*ACCOUNT_DELAY_RANGE)
            log("INFO", f"💤 等待 {delay:.1f} 秒后处理下一个账号")
            time.sleep(delay)

        task = TianYiYunPan(
            username=account["username"],
            password=account["password"],
            index=index,
        )
        results.append(task.run())

    return results


def format_notification(results):
    """生成多账号签到汇总通知。"""
    success_count = sum(result["success"] for result in results)
    failed_count = len(results) - success_count
    lines = [f"☁️ 天翼云盘签到：{success_count} 成功，{failed_count} 失败"]

    for index, result in enumerate(results, start=1):
        lines.extend((
            "",
            f"【账号 {index}】",
            f"👤 账号信息: {result['username']}",
            f"📊 签到状态: {result['status']}",
            "📦 网盘容量：",
            *[f"   {line}" for line in result["capacity"].splitlines()],
            f"🕐 完成时间: {result['completed_at']}",
        ))

    return "\n".join(lines)


def notify_user(title, content):
    """发送通知；没有通知模块时，将内容输出到统一格式日志。"""
    if notification_send is None:
        log("INFO", f"📢 {title}")
        for line in content.splitlines():
            log("INFO", line)
        return

    output = io.StringIO()
    try:
        with redirect_stdout(output), redirect_stderr(output):
            notification_send(title, content)
    except Exception as exc:
        log_captured_output(output.getvalue(), level="INFO")
        log("ERROR", f"❌ 通知发送失败：{exc}")
        return

    log_captured_output(output.getvalue(), level="INFO")
    log("SUCCESS", f"✅ 通知发送完成：{title}")


def main():
    log("INFO", "==== 天翼云盘签到开始 ====")

    try:
        accounts = load_accounts()
    except ValueError as exc:
        message = f"❌ 账号配置错误：{exc}"
        log("ERROR", message)
        notify_user("天翼云盘签到失败", message)
        return

    delay = (
        random.randint(0, MAX_RANDOM_DELAY)
        if RANDOM_SIGNIN and MAX_RANDOM_DELAY > 0
        else 0
    )
    if delay > 0:
        estimated_start = datetime.now() + timedelta(seconds=delay)
        log("INFO", f"🎲 随机延迟：{format_duration(delay)}")
        log("INFO", f"⏰ 预计开始时间：{estimated_start.strftime('%H:%M:%S')}")
        wait_with_countdown(delay, "天翼云盘签到")

    log("INFO", f"📝 共发现 {len(accounts)} 个账号，开始处理")

    results = process_accounts(accounts)
    notification = format_notification(results)
    notify_user("天翼云盘签到汇总", notification)

    log("INFO", "==== 天翼云盘签到完成 ====")


if __name__ == "__main__":
    main()
