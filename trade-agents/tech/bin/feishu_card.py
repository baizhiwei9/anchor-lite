#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""飞书交互式卡片 · 图文合成一条消息
=====================================================================
2026-10-23 用户要求：「不要一次推很多图，太乱了。也不要一次推一张图……
交互是卡片应该可以……之前这不有这个报告里不有文字吗？你就一起发吧，
只不过这个是配图而已」

→ 用飞书 **interactive 卡片**，把「文字报告 + 配图」放进**同一条消息**。
  卡片里图片用 img_key 内嵌（先调 im/v1/images 上传拿 key）。

关键 API：
  ① 上传图片  POST /open-apis/im/v1/images  （multipart，image_type=message）
              → data.image_key
  ② 发卡片    POST /open-apis/im/v1/messages?receive_id_type=chat_id
              msg_type=interactive, content={"config":..,"header":..,"elements":[...]}
  卡片图片元素：{"tag": "img", "img_key": "<key>", "alt": {"tag":"plain_text","content":"..."},
                "mode": "fit_horizontal", "preview": true}

用法（作为库调用）：
  import feishu_card as FC
  FC.send_card(title, blocks, imgs=[path1, path2], chat=CHAT)
  # blocks: [{"type":"md","text":"..."}, {"type":"hr"}]  按顺序排版
"""
import os, sys, json, urllib.request
from datetime import datetime

WS = os.environ.get("TECH_WS") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CHAT = os.environ.get("TECH_FEISHU_CHAT") or os.environ.get("CHAN_FEISHU_CHAT") or ""


def _cfg():
    """飞书应用凭据：优先环境变量，其次 workspace 同级 openclaw.json（本机部署形态）。"""
    aid = os.environ.get("FEISHU_APP_ID")
    sec = os.environ.get("FEISHU_APP_SECRET")
    if aid and sec:
        return aid, sec
    p = os.path.join(os.path.dirname(WS), "openclaw.json")
    c = json.load(open(p, encoding="utf-8"))["channels"]["feishu"]
    return c["appId"], c["appSecret"]


def token():
    aid, sec = _cfg()
    b = json.dumps({"app_id": aid, "app_secret": sec}).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=b, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())["tenant_access_token"]


def upload_image(tok, path):
    """上传图片拿 img_key。失败返回 None。"""
    boundary = "----x" + os.urandom(8).hex()
    raw = open(path, "rb").read()
    body = (("--%s\r\n" % boundary).encode() +
            b'Content-Disposition: form-data; name="image_type"\r\n\r\nmessage\r\n' +
            ("--%s\r\n" % boundary).encode() +
            b'Content-Disposition: form-data; name="image"; filename="i.png"\r\n'
            b"Content-Type: image/png\r\n\r\n" +
            raw + ("\r\n--%s--\r\n" % boundary).encode())
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/im/v1/images", data=body,
        headers={"Authorization": "Bearer " + tok,
                 "Content-Type": "multipart/form-data; boundary=" + boundary})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=90).read())
        if r.get("code") == 0:
            return r["data"]["image_key"]
        print("[WARN] 上传图片失败:", r.get("code"), r.get("msg"))
    except Exception as e:
        print("[WARN] 上传异常:", str(e)[:120])
    return None


def md_elem(text):
    return {"tag": "markdown", "content": text}


def hr_elem():
    return {"tag": "hr"}


def img_elem(key, alt=""):
    return {"tag": "img", "img_key": key,
            "alt": {"tag": "plain_text", "content": alt or "图"},
            "mode": "fit_horizontal", "preview": True}


def build_card(title, blocks, img_keys=None, template="blue", sign="📎 来自 OpenClaw.Deepseek"):
    """把文字块 + 图片按顺序拼成一张卡片
    blocks: [{"type":"md","text":...} | {"type":"hr"} | {"type":"img","key":...}]
    """
    els = []
    if sign:
        els.append(md_elem(sign))
    for b in blocks:
        t = b.get("type")
        if t == "md":
            els.append(md_elem(b["text"]))
        elif t == "hr":
            els.append(hr_elem())
        elif t == "img":
            els.append(img_elem(b["key"], b.get("alt", "")))
        elif t == "note":
            els.append({"tag": "note",
                        "elements": [{"tag": "plain_text", "content": b["text"]}]})
        elif t or b.get("tag"):
            # 2026-10-26 修 bug：原先只认 md/hr/img/note，
            # column_set（表格行）、div、table 等原生元素被**默默丢掉**
            # → 卡片发出去只剩标题 + 配图，正文表格全空（大佬 19:23 反馈「就是个图片」）
            # 另：`_cols()` 生成的行只有 "tag" 没有 "type"，这里一并兜住
            els.append({k: v for k, v in b.items() if k != "type"})
    return {"config": {"wide_screen_mode": True},
            "header": {"template": template,
                       "title": {"tag": "plain_text", "content": title}},
            "elements": els}


def send_card(title, blocks, imgs=None, chat=None, template="blue", sign=None):
    """发送图文合成卡片。imgs: [本地路径]，会自动上传并插到 blocks 里（若 blocks 里没指定位置，
    则统一放在末尾「配图」小节）。返回 True/False。
    """
    chat = chat or DEFAULT_CHAT
    try:
        tok = token()
    except Exception as e:
        print("[ERR] 取 token 失败:", str(e)[:120])
        return False

    blocks = list(blocks or [])
    if imgs:
        uploaded = []
        for p in imgs:
            k = upload_image(tok, p)
            if k:
                uploaded.append((k, os.path.basename(p)))
        if uploaded:
            blocks.append({"type": "hr"})
            blocks.append({"type": "md", "text": "**🖼 配图**"})
            for k, name in uploaded:
                blocks.append({"type": "img", "key": k, "alt": name})

    card = build_card(title, blocks, template=template, sign=sign)
    body = json.dumps({"receive_id": chat, "msg_type": "interactive",
                       "content": json.dumps(card, ensure_ascii=False)}).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        data=body, headers={"Authorization": "Bearer " + tok,
                            "Content-Type": "application/json"})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=60).read())
        ok = r.get("code") == 0
        print("[card]", "sent" if ok else "fail", r.get("code"), r.get("msg"))
        if not ok:
            print("[card] 详情:", json.dumps(r, ensure_ascii=False)[:300])
        return ok
    except Exception as e:
        print("[ERR] 发卡片异常:", str(e)[:200])
        return False
