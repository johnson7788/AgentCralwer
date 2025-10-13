import json
import uuid
import httpx
import time
from a2a.client import A2AClient
import asyncio
from a2a.types import (MessageSendParams, SendStreamingMessageRequest)

async def httpx_client():
    timeout = httpx.Timeout(30.0)
    async with httpx.AsyncClient(timeout=timeout) as httpx_client:
        client = await A2AClient.get_client_from_agent_card_url(
            httpx_client, 'http://localhost:10086'
        )
        send_message_payload = {
            'message': {
                'role': 'user',
                'parts': [{'type': 'text', 'text': prompt}],
                'messageId': request_id
            },
            # 关键：通过 metadata 指定需要的创新点个数
            'metadata': metadata
        }
        print(f"发送message信息: {send_message_payload}")
        streaming_request = SendStreamingMessageRequest(
            id=request_id,
            params=MessageSendParams(**send_message_payload)
        )
        stream_response = client.send_message_streaming(streaming_request)
        async for chunk in stream_response:
            print(time.time())
            print(chunk.model_dump(mode='json', exclude_none=True))
            chunk_json = chunk.model_dump(mode='json', exclude_none=True)
            print(chunk_json)

if __name__ == '__main__':
    # 示例输入
    # prompt = """请计算 (12+8)*3/2"""
    request_id = "123456"
    prompt = """把 10 miles 换算成 km"""
    metadata = {}
    asyncio.run(httpx_client())
