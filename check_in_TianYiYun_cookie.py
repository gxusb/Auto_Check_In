#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""天翼云盘 Cookie 会话签到入口。

TY_ACCOUNTS 支持 Cookie 会话或原有账号密码格式，例如：
TY_ACCOUNTS='[
  {"username":"账号1","cookie":"COOKIE字符串"},
  {"username":"账号2","cookies":"COOKIE字符串"},
  {"username":"账号3","password":"密码"}
]'

Cookie 会话优先使用 cookie/cookies 字段；未提供时回退到原脚本的账号密码登录。
Cookie 是敏感凭据，请勿分享或提交到版本控制。
"""

import json
import os
import random
import time

import check_in_TianYiYun as app


def load_accounts():
    """读取 Cookie 或账号密码格式的 TY_ACCOUNTS。"""
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
            raise ValueError(f"第 {index} 个账号必须是 JSON 对象")

        username = str(account.get("username") or f"账号{index}").strip()
        cookie = str(account.get("cookie") or account.get("cookies") or "").strip()
        password = str(account.get("password") or "")

        if not cookie and (not account.get("username") or not password):
            raise ValueError(
                f"第 {index} 个账号需要提供 cookie/cookies，"
                "或同时提供 username 和 password"
            )

        valid_accounts.append({
            "username": username,
            "password": password,
            "cookie": cookie,
        })

    return valid_accounts


_password_login = app.TianYiYunPan.login


def login(self):
    """优先以 Cookie 建立会话；未配置 Cookie 时沿用账号密码登录。"""
    cookie = getattr(self, "cookie", "")
    if not cookie:
        return _password_login(self)

    try:
        app.log("INFO", "🍪 使用 Cookie 会话", self.index)
        self.session.headers.update(app.LOGIN_HEADERS)
        # Cookie 字符串只写入请求头，不记录到日志。
        self.session.headers.update({"Cookie": cookie})
        return True
    except Exception as exc:
        app.log("ERROR", f"❌ Cookie 会话初始化失败：{exc}", self.index)
        return False


app.TianYiYunPan.login = login


def process_accounts(accounts):
    """处理账号并将各账号 Cookie 安全地绑定到独立会话。"""
    results = []

    for index, account in enumerate(accounts, start=1):
        if index > 1:
            delay = random.uniform(10, 30)
            app.log("INFO", f"💤 等待 {delay:.1f} 秒后处理下一个账号")
            time.sleep(delay)

        task = app.TianYiYunPan(
            username=account["username"],
            password=account.get("password", ""),
            index=index,
        )
        task.cookie = account.get("cookie", "")
        results.append(task.run())

    return results


app.load_accounts = load_accounts
app.process_accounts = process_accounts


if __name__ == "__main__":
    app.main()
