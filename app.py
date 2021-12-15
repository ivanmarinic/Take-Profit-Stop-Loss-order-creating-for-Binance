import hmac
import uuid
import  json, pprint

import websocket

import config, requests
import hashlib, time
from flask import Flask
from binance.client import Client
from binance.enums import *
from urllib.parse import urlencode
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)

SOCKET = "wss://fstream.binance.com/ws/"

base_url = "https://fapi.binance.com"

client = Client(config.API_KEY, config.API_SECRET)

array_of_orders = []


def dispatch_request(http_method):
    session = requests.Session()

    session.headers.update({
        'Content-Type': 'application/json;charset=utf-8',
        'X-MBX-APIKEY': config.API_KEY
    })
    return {
        'GET': session.get,
        'DELETE': session.delete,
        'PUT': session.put,
        'POST': session.post,
    }.get(http_method, 'POST')


def hashing(query_string):
    return hmac.new(config.API_SECRET.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()


def get_timestamp():
    return int(time.time() * 1000)


def send_signed_request(http_method, url_path, payload={}):
    query_string = urlencode(payload)
    # replace single quote to double quote
    query_string = query_string.replace('%27', '%22')
    if query_string:
        query_string = "{}&timestamp={}".format(query_string, get_timestamp())
    else:
        query_string = 'timestamp={}'.format(get_timestamp())

    url = base_url + url_path + '?' + query_string + '&signature=' + hashing(query_string)
    print("{} {}".format(http_method, url))
    params = {'url': url, 'params': {}}
    response = dispatch_request(http_method)(**params)
    return response.json()


def send_public_request(http_method, url_path, payload={}):
    query_string = urlencode(payload, True)
    url = base_url + url_path
    if query_string:
        url = url + '?' + query_string
    print("{}".format(url))
    response = dispatch_request(http_method)(url=url)
    return response.json()


def get_open_positions(params):
    response = send_signed_request('GET', '/fapi/v2/positionRisk', params)
    return response


def get_all_orders(params):
    response = send_signed_request('GET', '/fapi/v1/allOrders', params)
    return response


# works for spot trading only
def get_listen_key_by_REST():
    url = 'https://api.binance.com/api/v3/userDataStream'
    response = requests.post(url, headers={'X-MBX-APIKEY': config.API_KEY})  # ['listenKey']
    json = response.json()

    return json['listenKey']

def put_listen_key():
    response_post = send_public_request('PUT', '/fapi/v1/listenKey')
    print(response_post)

def on_open(ws):
    print('opened connection')

    response_post = send_public_request('POST', '/fapi/v1/listenKey')
    print(response_post)

    scheduler = BackgroundScheduler()
    scheduler.add_job(func=put_listen_key, trigger="interval", minutes=50)
    scheduler.start()


def is_valid_uuid(val):
    try:
        uuid.UUID(str(val))
        return True
    except ValueError:
        return False


def on_close(ws, close_status_code, close_msg):
    print("on_close args:")
    if close_status_code or close_msg:
        print("close status code: " + str(close_status_code))
        print("close message: " + str(close_msg))

    listen_key = client.futures_stream_get_listen_key()
    ws = websocket.WebSocketApp(SOCKET + listen_key, on_open=on_open, on_close=on_close, on_message=on_message,
                                on_error=on_error)
    ws.run_forever()


def on_message(ws, message):
    print('received message')
    json_message = json.loads(message)
    pprint.pprint(json_message)

    params = {
        "symbol": json_message['o']['s']
    }
    open_positions = get_open_positions(params)
    open_orders = get_all_orders(params)

    if(float(json_message['o']['L']) <= 1 and float(json_message['o']['L']) >= 0.1):
        round_number = 4

    if (float(json_message['o']['L']) < 0.1):
        round_number = 5

    if (float(json_message['o']['L']) >= 1):
        round_number = 3

    if (float(json_message['o']['L']) >= 100):
        round_number = 2


    stop_loss = float(json_message['o']['L']) * 0.986
    stop_loss = float(round(stop_loss, round_number))

    take_profit = float(json_message['o']['L']) * 1.016
    take_profit = float(round(take_profit, round_number))

    stop_loss_short = float(json_message['o']['L']) * 1.013
    stop_loss_short = float(round(stop_loss_short, round_number))

    take_profit_short = float(json_message['o']['L']) * 0.985
    take_profit_short = float(round(take_profit_short, round_number))

    uniqueId_1 = uuid.uuid1()
    uniqueId_2 = uuid.uuid1()

    if json_message['e'] == 'ORDER_TRADE_UPDATE' and \
            json_message['o']['L'] != '0' and \
            json_message['o']['X'] == 'FILLED' and \
            json_message['o']['S'] == 'BUY' and \
            json_message['o']['ot'] == 'MARKET':

        # if position is open, only then orders can be created
        if (float(open_positions[0]['positionAmt']) > 0.0):
            order_1 = client.futures_create_order(
                symbol=json_message['o']['s'],
                side='SELL',
                type='STOP_MARKET',
                quantity=json_message['o']['q'],
                # quantity=abs(float(open_positions[0]['positionAmt'])),
                stopPrice=stop_loss,
                price=stop_loss,
                reduceOnly=True,
                newClientOrderId=uniqueId_1
            )

            order_2 = client.futures_create_order(
                symbol=json_message['o']['s'],
                side='SELL',
                type='TAKE_PROFIT',
                quantity=json_message['o']['q'],
                # quantity=abs(float(open_positions[0]['positionAmt'])),
                stopPrice=take_profit,
                price=take_profit,
                reduceOnly=True,
                newClientOrderId=uniqueId_2
            )


        if (float(open_positions[0]['positionAmt']) < 0.0):
            for orders in open_orders:
                if orders['status'] == 'NEW' and \
                        orders['symbol'] == json_message['o']['s']:
                    params = {
                        "symbol": orders['symbol'],
                        "orderId": orders['orderId']
                    }

                    response = send_signed_request('DELETE', '/fapi/v1/order', params)
                    print(response)

            if (float(json_message['o']['L']) <= 1 and float(json_message['o']['L']) >= 0.1):
                round_number = 4

            if (float(json_message['o']['L']) < 0.1):
                round_number = 5

            if (float(json_message['o']['L']) >= 1):
                round_number = 3

            if (float(json_message['o']['L']) >= 100):
                round_number = 2

            stop_loss_short_after_buy = float(open_positions[0]['entryPrice']) * 1.013
            stop_loss_short_after_buy = float(round(stop_loss_short_after_buy, round_number))

            take_profit_short_after_buy = float(open_positions[0]['entryPrice']) * 0.985
            take_profit_short_after_buy = float(round(take_profit_short_after_buy, round_number))

            order_1 = client.futures_create_order(
                symbol=json_message['o']['s'],
                side=SIDE_BUY,
                type='STOP_MARKET',
                quantity=abs(float(open_positions[0]['positionAmt'])),
                stopPrice=stop_loss_short_after_buy,
                price=stop_loss_short_after_buy,
                reduceOnly=True,
                newClientOrderId=uniqueId_1
            )

            order_2 = client.futures_create_order(
                symbol=json_message['o']['s'],
                side=SIDE_BUY,
                type='TAKE_PROFIT',
                quantity=abs(float(open_positions[0]['positionAmt'])),
                stopPrice=take_profit_short_after_buy,
                price=take_profit_short_after_buy,
                reduceOnly=True,
                newClientOrderId=uniqueId_2
            )

        list_of_orders = []

        list_of_orders.append(order_1)
        list_of_orders.append(order_2)

        array_of_orders.append(list_of_orders)

    # print(array_of_orders)

    if json_message['e'] == 'ORDER_TRADE_UPDATE' and \
            json_message['o']['L'] != '0' and \
            json_message['o']['X'] == 'FILLED' and \
            json_message['o']['S'] == 'SELL' and \
            json_message['o']['ot'] == 'MARKET':

        #if position is open, only then orders can be created
        if (float(open_positions[0]['positionAmt']) < 0.0):
            order_1 = client.futures_create_order(
                symbol=json_message['o']['s'],
                side='BUY',
                type='STOP_MARKET',
                quantity=json_message['o']['q'],
                # quantity=abs(float(open_positions[0]['positionAmt'])),
                stopPrice=stop_loss_short,
                price=stop_loss_short,
                reduceOnly=True,
                newClientOrderId=uniqueId_1
            )

            order_2 = client.futures_create_order(
                symbol=json_message['o']['s'],
                side='BUY',
                type='TAKE_PROFIT',
                quantity=json_message['o']['q'],
                # quantity=abs(float(open_positions[0]['positionAmt'])),
                stopPrice=take_profit_short,
                price=take_profit_short,
                reduceOnly=True,
                newClientOrderId=uniqueId_2
            )

        if (float(open_positions[0]['positionAmt']) > 0.0):
            for orders in open_orders:
                if orders['status'] == 'NEW' and \
                        orders['symbol'] == json_message['o']['s']:
                    params = {
                        "symbol": orders['symbol'],
                        "orderId": orders['orderId']
                    }

                    response = send_signed_request('DELETE', '/fapi/v1/order', params)
                    print(response)

            if (float(json_message['o']['L']) <= 1 and float(json_message['o']['L']) >= 0.1):
                round_number = 4

            if (float(json_message['o']['L']) < 0.1):
                round_number = 5

            if (float(json_message['o']['L']) >= 1):
                round_number = 3

            if (float(json_message['o']['L']) >= 100):
                round_number = 2

            stop_loss_after_sell = float(open_positions[0]['entryPrice']) * 0.986
            stop_loss_after_sell = float(round(stop_loss_after_sell, round_number))

            take_profit_after_sell = float(open_positions[0]['entryPrice']) * 1.016
            take_profit_after_sell = float(round(take_profit_after_sell, round_number))

            order_1 = client.futures_create_order(
                symbol=json_message['o']['s'],
                side=SIDE_SELL,
                type='STOP_MARKET',
                quantity=abs(float(open_positions[0]['positionAmt'])),
                stopPrice=stop_loss_after_sell,
                price=stop_loss_after_sell,
                reduceOnly=True,
                newClientOrderId=uniqueId_1
            )

            order_2 = client.futures_create_order(
                symbol=json_message['o']['s'],
                side=SIDE_SELL,
                type='TAKE_PROFIT',
                quantity=abs(float(open_positions[0]['positionAmt'])),
                stopPrice=take_profit_after_sell,
                price=take_profit_after_sell,
                reduceOnly=True,
                newClientOrderId=uniqueId_2
            )

        list_of_orders = []

        list_of_orders.append(order_1)
        list_of_orders.append(order_2)

        array_of_orders.append(list_of_orders)

    # print(array_of_orders)

    # CANCELED/FILLED
    if json_message['e'] == 'ORDER_TRADE_UPDATE' and \
            json_message['o']['X'] == 'FILLED' and \
            json_message['o']['S'] == 'SELL' and \
            is_valid_uuid(json_message['o']['c']) == True:
            #and \ json_message['o']['rp'] != '0':

        # get 2 orders from a list; 1 is FILLED; CANCEL the other one
        #order_pair = []

        for orders in open_orders:
            if orders['status'] == 'NEW' and \
                    json_message['o']['q'] == orders['origQty']:
                params = {
                    "symbol": orders['symbol'],
                    "orderId": orders['orderId']
                }
                response = send_signed_request('DELETE', '/fapi/v1/order', params)
                print(response)

    # CANCELED/FILLED
    if json_message['e'] == 'ORDER_TRADE_UPDATE' and \
            json_message['o']['X'] == 'FILLED' and \
            json_message['o']['S'] == 'BUY' and \
            is_valid_uuid(json_message['o']['c']) == True:

        for orders in open_orders:
            if orders['status'] == 'NEW' and \
                    json_message['o']['q'] == orders['origQty']:
                params = {
                    "symbol": orders['symbol'],
                    "orderId": orders['orderId']
                }
                response = send_signed_request('DELETE', '/fapi/v1/order', params)
                print(response)

    #if there are open orders when there's no open position, delete all of the orders
    for orders in open_orders:
        if orders['status'] == 'NEW':
            if float(open_positions[0]['positionAmt']) == 0.0:
                params = {
                    "symbol": orders['symbol'],
                    "orderId": orders['orderId']
                }

                response = send_signed_request('DELETE', '/fapi/v1/order', params)
                print(response)


def on_error(ws, error):
    print(error)


listen_key = client.futures_stream_get_listen_key()
ws = websocket.WebSocketApp(SOCKET + listen_key, on_open=on_open, on_close=on_close, on_message=on_message,
                            on_error=on_error)
ws.run_forever()
