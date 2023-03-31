from Crypto.Cipher import AES
import hashlib
import json
import base64
import aiohttp
import asyncio

from llmapi_cli.llmclient import LLMClient

from pydantic import BaseModel
from fastapi import FastAPI, Request, BackgroundTasks
app = FastAPI()


class AESCipher(object):
    def __init__(self, key):
        self.bs = AES.block_size
        self.key = hashlib.sha256(AESCipher.str_to_bytes(key)).digest()

    @staticmethod
    def str_to_bytes(data):
        u_type = type(b"".decode('utf8'))
        if isinstance(data, u_type):
            return data.encode('utf8')
        return data

    @staticmethod
    def _unpad(s):
        return s[:-ord(s[len(s) - 1:])]

    def decrypt(self, enc):
        iv = enc[:AES.block_size]
        cipher = AES.new(self.key, AES.MODE_CBC, iv)
        return self._unpad(cipher.decrypt(enc[AES.block_size:]))

    def decrypt_string(self, enc):
        enc = base64.b64decode(enc)
        return self.decrypt(enc).decode('utf8')


class TokenManager():
    def __init__(self, app_id, app_secret) -> None:
        self.token = 'an_invalid_token'
        self.url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        self.req = {
            "app_id": app_id,
            "app_secret": app_secret
        }

    async def update(self):
        async with aiohttp.ClientSession() as session:
            async with session.post(self.url, headers={
                'Content-Type': 'application/json; charset=utf-8'
            }, data=json.dumps(self.req), timeout=5) as response:
                data = await response.json()
                if (data["code"] == 0):
                    self.token = data["tenant_access_token"]

    def get_token(self):
        return self.token


class LarkMsgSender():
    def __init__(self, token_manager: TokenManager) -> None:
        self.prefix = "https://open.feishu.cn/open-apis/im/v1/messages/"
        self.suffix = "/reply"
        self.token_manager = token_manager

    async def send(self, msg, msg_id):
        url = self.prefix + msg_id + self.suffix
        headers = {
            'Authorization': 'Bearer ' + self.token_manager.get_token(),  # your access token
            'Content-Type': 'application/json'
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, data=json.dumps({
                "msg_type": "text",
                "content": json.dumps({
                    "text": msg,
                })
            })) as response:
                data = await response.json()
        if (data["code"] == 99991668 or data["code"] == 99991663):  # token expired
            await self.token_manager.update()
            await self.send(msg, msg_id)
        elif (data["code"] == 0):
            return
        else:
            print("unreachable")
            print(data)
            pass


# 将下面的参数改为从json文件中读取
config = json.load(open('.env.json'))
app_id = config['app_id']
app_secret = config['app_secret']
verification_token = config['verification_token']
encryption_key = config['encryption_key']

cipher = AESCipher(encryption_key)
users_info = {}
token_manager = TokenManager(app_id=app_id, app_secret=app_secret)
sender = LarkMsgSender(token_manager)


class UserInfo():
    llmapi_key = None
    llmapi_host = 'https://api.llmapi.io'
    client = None
    bot = 'chatgpt'


async def reply_message(input: dict):
    reply = ""

    if input['header']['token'] != verification_token:
        return

    # 检查输入中是否包含文本消息
    if 'event' in input and 'message' in input['event'] and 'content' in input['event']['message']:
        try:
            content = json.loads(input['event']['message']['content'])
            if 'text' not in content:
                reply = "不是纯文本消息"
        except ValueError:
            reply = "消息格式错误"
    if reply != "":
        await sender.send(reply, input["event"]["message"]["message_id"])
        return

    user_id = input['event']['sender']['sender_id']['user_id']
    if user_id not in users_info:
        users_info[user_id] = UserInfo()

    prompt = content['text']

    help_msg = '\n'
    help_msg += '发送 "!help" 给机器人，机器人会显示帮助信息\n'
    help_msg += '发送 "!reset" 给机器人，机器人会重置对话状态\n'
    help_msg += '发送 "!llmapi_host http://your.llmapi.host.com" 给机器人，设置llmapi地址，默认为https://api.llmapi.io\n'
    help_msg += '发送 "!llmapi_key xxx" 给机器人，设置llmapi key，如果是自建llmapi，则无需设置\n'
    help_msg += '发送 "!bot xxx" 给机器人，指定对应机器人类型，比如chatgpt、welm、newbing...\n'
    help_msg += '发送 "!show" 给机器人，展示当前状态\n'
    help_msg += '\n'
    help_msg += '用法：\n'
    help_msg += '如果你是自建llmapi，那么你需要先发送 "!llmapi_host http://your.llmapi.host.com" 给机器人，设置llmapi地址\n'
    help_msg += '如果你是使用官方llmapi，那么你需要先发送 "!llmapi_key xxx" 给机器人，设置llmapi key，apikey在https://llmapi.io获取\n'
    help_msg += '默认是chatgpt，如需更改机器人，通过!bot指定，比如 "!bot welm"，目前支持的机器人有：chatgpt、welm、newbing\n'
    help_msg += '然后发送任意消息给机器人，机器人会自动回复\n'
    help_msg += '如果你想重置对话状态，可以发送 "!reset" 给机器人\n'
    help_msg += '如果你想查看当前状态，可以发送 "!show" 给机器人，遇到问题时这个命令很有帮助哦\n'

    def get_key(key):
        if key is None:
            return 'None'
        else:
            return key[0:3] + '*' * (len(key) - 6) + key[-3:]
    if prompt == '!help':
        reply = help_msg
    elif prompt == '!show':
        reply = '当前状态：\n'
        reply += 'llmapi地址：' + users_info[user_id].llmapi_host + '\n'
        reply += 'llmapi key：' + get_key(users_info[user_id].llmapi_key) + '\n'
        reply += '机器人类型：' + users_info[user_id].bot + '\n'
        reply += '对话状态：' + \
            ('已初始化' if users_info[user_id].client is not None else '未初始化') + '\n'
    elif prompt == '!reset':
        if users_info[user_id].client is not None:
            await users_info[user_id].client.end_session()
        users_info[user_id].client = None
        reply = '重置对话状态\n'
    elif prompt.startswith('!llmapi_host'):
        if len(prompt.split(' ')) != 2:
            reply = '参数错误，正确用法：!llmapi_host http://your.llmapi.host.com\n'
        elif not prompt.split(' ')[1].startswith('http'):
            reply = '参数错误，正确用法：!llmapi_host http://your.llmapi.host.com\n'
        else:
            users_info[user_id].llmapi_host = prompt.split(' ')[1]
            reply = '设置llmapi地址为' + users_info[user_id].llmapi_host + '\n'
    elif prompt.startswith('!llmapi_key'):
        if len(prompt.split(' ')) != 2:
            reply = '参数错误，正确用法：!llmapi_key xxx\n'
        else:
            users_info[user_id].llmapi_key = prompt.split(' ')[1]
            reply = '设置llmapi key为' + \
                get_key(users_info[user_id].llmapi_key) + '\n'
            reply += '建议把你刚刚发的消息删除，防止泄露llmapi key\n'
    elif prompt.startswith('!bot'):
        users_info[user_id].bot = prompt.split(' ')[1]
        reply = '设置机器人为' + users_info[user_id].bot + '\n'
    else:
        if users_info[user_id].client is None:
            users_info[user_id].client = LLMClient(
                apikey=users_info[user_id].llmapi_key,
                host=users_info[user_id].llmapi_host,
                bot_type=users_info[user_id].bot
            )
            if not await users_info[user_id].client.start_session():
                users_info[user_id].client = None
                reply = "启动会话失败\n" + help_msg
        if users_info[user_id].client is not None:
            success, reply = await users_info[user_id].client.ask(
                prompt=prompt, timeout=300)
            if success != 0:
                reply = "请求回复失败"
    await sender.send(reply, input["event"]["message"]["message_id"])


class LarkMsgType(BaseModel):
    encrypt: str


processed_message_ids = set()


@app.post("/")
async def process(message: LarkMsgType, request: Request, background_tasks: BackgroundTasks):
    plaintext = json.loads(cipher.decrypt_string(message.encrypt))
    if 'challenge' in plaintext:  # url verification
        return {'challenge': plaintext['challenge']}

    message_id = plaintext['event']['message']['message_id']
    if message_id not in processed_message_ids:
        # 将message_id加入到已处理列表，避免下次重复处理
        processed_message_ids.add(message_id)
        background_tasks.add_task(
            reply_message, plaintext)  # reply in background

    return {'message': 'ok'}  # 接受到消息后，立即返回ok，避免客户端重试
