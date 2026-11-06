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
FilePath: \Auto_Check_In\check_in_Quark.py
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
import os
import re
from urllib.parse import parse_qs, urlparse

import requests


# -------------------- 配置 --------------------

ENV_NAME = "COOKIE_QUARK"
REQUEST_TIMEOUT = 15

try:
    from notify import send as notification_send
except Exception as err:
    notification_send = None
    print(f"{err}\n❌ 加载通知服务失败")


# -------------------- 账号解析 --------------------


def get_accounts():
    """读取并解析环境变量中的多个账号。"""
    raw_value = os.environ.get(ENV_NAME)
    if not raw_value:
        return []

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

    def _query_params(self):
        """构造接口通用查询参数。"""
        params = {
            "pr": "ucpro",
            "fr": "android",
        }
        params.update({
            key: self.param.get(key, "")
            for key in self.QUERY_PARAMS
        })
        return params

    def _request_json(self, method, url, **kwargs):
        """发送请求并解析 JSON；请求或解析失败时返回错误信息。"""
        try:
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
    def convert_bytes(byte_count):
        """将字节数转换为易读的容量单位。"""
        units = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
        value = byte_count or 0
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

        if isinstance(data, dict) and data.get("data"):
            return data["data"].get("balance")

        if isinstance(data, dict):
            return data.get("msg", data.get("message", "未知错误"))

        return "接口返回格式异常"

    def do_sign(self):
        """检查签到状态；若今日尚未签到则执行签到。"""
        growth_info = self.get_growth_info()
        if not growth_info:
            return "❌ 签到异常：获取成长信息失败\n"

        user = self.param.get("user", "未命名账号")
        membership = "88VIP" if growth_info.get("88VIP") else "普通用户"
        cap_composition = growth_info.get("cap_composition") or {}
        cap_sign = growth_info.get("cap_sign") or {}

        sign_capacity = cap_composition.get("sign_reward", 0)
        log = (
            f" {membership} {user}\n"
            f"💾 网盘总容量："
            f"{self.convert_bytes(growth_info.get('total_capacity', 0))}，"
            f"签到累计容量：{self.convert_bytes(sign_capacity)}\n"
        )

        progress = cap_sign.get("sign_progress", 0)
        target = cap_sign.get("sign_target", 0)

        if cap_sign.get("sign_daily"):
            reward = cap_sign.get("sign_daily_reward", 0)
            log += (
                f"✅ 签到日志：今日已签到+{self.convert_bytes(reward)}，"
                f"连签进度({progress}/{target})\n"
            )
            return log

        success, result = self.get_growth_sign()
        if success:
            log += (
                f"✅ 执行签到：今日签到+{self.convert_bytes(result)}，"
                f"连签进度({progress + 1}/{target})\n"
            )
        else:
            log += f"❌ 签到异常：{result}\n"

        return log


# -------------------- 批量处理与通知 --------------------


def process_accounts(accounts):
    """依次处理账号并收集签到结果。"""
    results = []

    for index, user_data in enumerate(accounts, start=1):
        result = f"🙍🏻‍♂️ 第{index}个账号"
        result += Quark(user_data).do_sign()
        results.append(result)

    return results


def format_notification(results):
    """生成多账号签到汇总通知。"""
    return "\n".join(results)


def notify_user(title, content):
    """发送签到结果通知；失败时输出错误信息。"""
    if notification_send is None:
        return

    try:
        notification_send(title, content)
    except Exception as err:
        print(f"{err}\n❌ 通知发送失败，请查看运行日志")


# -------------------- 主流程 --------------------


def main():
    accounts = get_accounts()

    if not accounts:
        message = f"❌ 未添加 {ENV_NAME} 环境变量或账号信息为空"
        print(message)
        notify_user("夸克自动签到", message)
        return message

    print(f"✅ 检测到共 {len(accounts)} 个夸克账号\n")

    results = process_accounts(accounts)
    message = format_notification(results)
    print(message)
    notify_user("夸克自动签到", message)

    return message


if __name__ == "__main__":
    print("----------夸克网盘开始签到----------")
    main()
    print("----------夸克网盘签到完毕----------")