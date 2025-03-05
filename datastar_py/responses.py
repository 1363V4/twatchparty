from .sse import SSE_HEADERS, ServerSentEventGenerator
from quart import make_response


async def make_datastar_quart_response(generator):
    response = await make_response(generator(ServerSentEventGenerator), SSE_HEADERS)
    response.timeout = None
    return response
