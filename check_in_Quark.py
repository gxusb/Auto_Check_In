'''
new Env('夸克自动签到')
cron: 0 9 * * *

V2版-目前有效
使用移动端接口修复每日自动签到，移除原有的“登录验证”，参数有效期未知

V1版-已失效
受大佬 @Cp0204 的仓库项目启发改编
源码来自 GitHub 仓库：https://github.com/Cp0204/quark-auto-save
提取“登录验证”“签到”“领取”方法封装到下文中的“Quark”类中

Author: BNDou
Date: 2024-03-15 21:43:06
LastEditTime: 2025-11-18 03:49:26
FilePath: \\Auto_Check_In\\check_in_Quark.py
Description: 
抓包流程：
    【手机端】
    ①打开抓包，手机端访问抽奖页
    ②找到url为 https://drive-m.quark.cn/1/clouddrive/act/growth/reward 的请求信息
    ③复制整段url，该链接后面必须要有参数: kps sign vcode，粘贴到环境变量
    环境变量名为 COOKIE_QUARK 多账户用 回车 或 && 分开
    user字段是用户名 (可是随意填写，多账户方便区分)
    例如: user=张三; url=https://drive-m.quark.cn/1/clouddrive/act/growth/reward?xxxxxx=xxxxxx&kps=abcdefg&sign=hijklmn&vcode=111111111;
    旧版环境变量格式也兼容，例如: user=张三; kps=abcdefg; sign=hijklmn; vcode=111111111;
'''
import io
import math
import os
import re
import unicodedata
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import requests


# -------------------- 配置 --------------------

ENV_NAME = "COOKIE_QUARK"
REQUEST_TIMEOUT = 15
GROWTH_REQUEST_PATHS = ("/capacity/growth/info", "/capacity/growth/sign")


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
        f"{pad_display(level_text, 10)} "
        f"{pad_display(account_text, 10)} "
        f"{message}"
    )


def log_captured_output(output, level="INFO"):
    """将依赖模块直接写入 stdout/stderr 的内容转换为统一日志格式。"""
    for line in output.splitlines():
        if line.strip():
            log(level, line.strip())


try:
    from notify import send as notification_send
except Exception as err:
    notification_send = None
    log("ERROR", f"❌ 加载通知服务失败：{err}")


# -------------------- 账号解析 --------------------


def get_accounts():
    """读取并解析环境变量中的多个账号。"""
    raw_value = os.environ.get(ENV_NAME)
    if not raw_value:
        return []

    # 多账号支持换行或 && 分隔；单个账号内的字段使用分号分隔。
    account_strings = re.split(r"\n|&&", raw_value)
    return [parse_account(item) for item in account_strings if item.strip()]


def parse_account(account_string):
    """解析单个账号的分号分隔字段，并从 URL 中补充签到参数。"""
    account = {}

    for item in account_string.split(";"):
        key, separator, value = item.partition("=")
        if separator:
            account[key.strip()] = value.strip()

    if account.get("url"):
        # 完整请求 URL 中的参数优先于单独填写的同名字段。
        account.update(extract_params(account["url"]))

    return account


def extract_params(url):
    """从请求 URL 中提取 kps、sign 和 vcode。"""
    query = parse_qs(urlparse(url).query)
    return {
        key: query.get(key, [""])[0]
        for key in ("kps", "sign", "vcode")
    }


# -------------------- 夸克网盘签到 --------------------


class Quark:
    """封装夸克网盘签到相关请求。"""

    BASE_URL = "https://drive-m.quark.cn/1/clouddrive"
    QUERY_PARAMS = ("kps", "sign", "vcode")

    def __init__(self, user_data):
        self.param = user_data
        self.session = requests.Session()
        self.request_time = None

    def _query_params(self):
        """构造接口通用查询参数。"""
        return {
            "pr": "ucpro",
            "fr": "android",
            **{key: self.param.get(key, "") for key in self.QUERY_PARAMS},
        }

    def _request_json(self, method, url, **kwargs):
        """发送请求并解析 JSON；请求或解析失败时返回错误信息。"""
        try:
            if url.endswith(GROWTH_REQUEST_PATHS):
                # 在签到相关请求发起时记录时间，供最终通知使用。
                self.request_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            response = self.session.request(
                method,
                url,
                timeout=REQUEST_TIMEOUT,
                **kwargs,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as err:
            return {"message": f"请求失败：{err}"}
        except ValueError:
            return {"message": "接口返回的内容不是有效 JSON"}

    @staticmethod
    def _numeric_value(value):
        """将有限的数值或数字字符串转换为浮点数。"""
        if isinstance(value, bool):
            return None

        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None

        return number if math.isfinite(number) else None

    @classmethod
    def _format_number(cls, value):
        number = cls._numeric_value(value)
        if number is None:
            return "?"
        return f"{number:g}"

    @classmethod
    def convert_bytes(cls, byte_count):
        """将字节数转换为易读的容量单位；无效数值不参与换算。"""
        units = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
        if byte_count is None or byte_count == "":
            value = 0
        else:
            value = cls._numeric_value(byte_count)
            if value is None or value < 0:
                return "N/A"

        unit_index = 0

        while value >= 1024 and unit_index < len(units) - 1:
            value /= 1024
            unit_index += 1

        return f"{value:.2f} {units[unit_index]}"

    def get_growth_info(self):
        """获取用户当前的签到及容量信息。"""
        data = self._request_json(
            "GET",
            f"{self.BASE_URL}/capacity/growth/info",
            params=self._query_params(),
        )
        return data.get("data") if isinstance(data, dict) else None

    def get_growth_sign(self):
        """执行签到并返回签到奖励，或返回接口错误信息。"""
        data = self._request_json(
            "POST",
            f"{self.BASE_URL}/capacity/growth/sign",
            params=self._query_params(),
            json={"sign_cyclic": True},
        )

        if not isinstance(data, dict):
            return False, "接口返回格式异常"

        result = data.get("data")
        if result:
            return True, result.get("sign_daily_reward", 0)

        return False, data.get("message", "未知错误")

    def query_balance(self):
        """查询抽奖余额。"""
        params = {
            "moduleCode": "1f3563d38896438db994f118d4ff53cb",
            "kps": self.param.get("kps", ""),
        }
        data = self._request_json(
            "GET",
            "https://coral2.quark.cn/currency/v1/queryBalance",
            params=params,
        )

        if not isinstance(data, dict):
            return "接口返回格式异常"

        if data.get("data"):
            return data["data"].get("balance")

        return data.get("msg", data.get("message", "未知错误"))

    def _format_capacity_summary(self, growth_info):
        """格式化账号身份及当前容量信息。"""
        user = self.param.get("user", "未命名账号")
        membership = "88VIP" if growth_info.get("88VIP") else "普通用户"
        cap_composition = growth_info.get("cap_composition") or {}
        sign_capacity = cap_composition.get("sign_reward", 0)
        return (
            f" {membership} {user}\n"
            f"💾 网盘总容量："
            f"{self.convert_bytes(growth_info.get('total_capacity', 0))}，"
            f"签到累计容量：{self.convert_bytes(sign_capacity)}\n"
        )

    def do_sign(self):
        """检查签到状态；若今日尚未签到则执行签到。"""
        growth_info = self.get_growth_info()
        if not growth_info:
            return "❌ 签到异常：获取成长信息失败\n"

        cap_sign = growth_info.get("cap_sign") or {}
        progress = self._numeric_value(cap_sign.get("sign_progress", 0))
        target = self._numeric_value(cap_sign.get("sign_target", 0))
        progress_text = self._format_number(progress)
        target_text = self._format_number(target)
        capacity_log = self._format_capacity_summary(growth_info)

        # 已签到时保留接口返回的当日奖励，不重复提交签到请求。
        if cap_sign.get("sign_daily"):
            reward = cap_sign.get("sign_daily_reward", 0)
            capacity_log += (
                f"✅ 签到日志：今日已签到+{self.convert_bytes(reward)}，"
                f"连签进度({progress_text}/{target_text})\n"
            )
            return capacity_log

        success, result = self.get_growth_sign()
        if not success:
            return capacity_log + f"❌ 签到异常：{result}\n"

        next_progress = (
            self._format_number(progress + 1) if progress is not None else "?"
        )
        capacity_log += (
            f"✅ 执行签到：今日签到+{self.convert_bytes(result)}，"
            f"连签进度({next_progress}/{target_text})\n"
        )
        return capacity_log


# -------------------- 批量处理与通知 --------------------


def process_accounts(accounts):
    """依次处理账号并收集签到结果。"""
    results = []
    latest_request_time = None

    for index, user_data in enumerate(accounts, start=1):
        account_name = user_data.get("user", f"未命名账号{index}")
        log("INFO", f"🙍🏻‍♂️ 处理第 {index} 个账号：{account_name}", index)

        quark = Quark(user_data)
        sign_result = quark.do_sign()
        if quark.request_time:
            # 使用最近一个账号实际发起的成长信息或签到请求时间。
            latest_request_time = quark.request_time
        account_result = f"🙍🏻‍♂️ 第{index}个账号{sign_result}"
        results.append(account_result)

        for line in sign_result.splitlines():
            if line.strip():
                level = "ERROR" if "❌" in line else "INFO"
                log(level, line.strip(), index)

    return results, latest_request_time


def format_notification(results, request_time=None):
    """生成多账号签到汇总通知。"""
    content = "\n".join(results)
    if request_time:
        content += f"\n🕐 完成时间: {request_time}"
    return content


def notify_user(title, content):
    """发送签到结果通知；将通知模块的输出也转换为统一日志格式。"""
    if notification_send is None:
        log("INFO", f"📢 {title}")
        for line in content.splitlines():
            log("INFO", line)
        return

    output = io.StringIO()
    try:
        with redirect_stdout(output), redirect_stderr(output):
            notification_send(title, content)
    except Exception as err:
        log_captured_output(output.getvalue(), level="INFO")
        log("ERROR", f"❌ 通知发送失败：{err}")
        return

    log_captured_output(output.getvalue(), level="INFO")
    log("SUCCESS", f"✅ 通知发送完成：{title}")


# -------------------- 主流程 --------------------


def main():
    """运行主流程并处理签到结果。"""
    title = "夸克自动签到"
    log("INFO", "==== 夸克网盘签到开始 ====")

    accounts = get_accounts()
    if not accounts:
        message = f"❌ 未添加 {ENV_NAME} 环境变量或账号信息为空"
        log("ERROR", message)
        notify_user(title, message)
        return message

    log("INFO", f"📝 共发现 {len(accounts)} 个账号，开始处理")

    results, request_time = process_accounts(accounts)
    message = format_notification(results, request_time)
    notify_user(title, message)

    return message


if __name__ == "__main__":
    main()
    log("INFO", "==== 夸克网盘签到完成 ====")
