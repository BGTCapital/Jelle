import asyncio
import contextlib
import logging
import unittest
import time
from decimal import Decimal
from typing import List

from hummingbot.core.clock import Clock
from hummingbot.core.clock_mode import ClockMode
from hummingbot.core.data_type.order_book_tracker import OrderBookTrackerDataSourceType
from hummingbot.core.data_type.user_stream_tracker import UserStreamTrackerDataSourceType
from hummingbot.core.event.event_logger import EventLogger
from hummingbot.core.event.events import (
    OrderType,
    MarketEvent,
    BuyOrderCreatedEvent,
    SellOrderCreatedEvent,
    OrderCancelledEvent
)
from hummingbot.core.network_iterator import NetworkStatus
from hummingbot.logger.struct_logger import METRICS_LOG_LEVEL
from hummingbot.market.binance_perpetual.binance_perpetual_market import BinancePerpetualMarket
from hummingbot.core.utils.async_utils import safe_ensure_future, safe_gather
from .assets.test_keys import Keys

logging.basicConfig(level=METRICS_LOG_LEVEL)


class BinancePerpetualMarketUnitTest(unittest.TestCase):
    events: List[MarketEvent] = [
        MarketEvent.ReceivedAsset,
        MarketEvent.BuyOrderCompleted,
        MarketEvent.SellOrderCompleted,
        MarketEvent.OrderFilled,
        MarketEvent.TransactionFailure,
        MarketEvent.BuyOrderCreated,
        MarketEvent.SellOrderCreated,
        MarketEvent.OrderCancelled,
        MarketEvent.OrderFailure
    ]

    market: BinancePerpetualMarket
    market_logger: EventLogger
    stack: contextlib.ExitStack

    @classmethod
    def setUpClass(cls) -> None:
        cls._ev_loop = asyncio.get_event_loop()
        cls.clock: Clock = Clock(ClockMode.REALTIME)
        cls.market: BinancePerpetualMarket = BinancePerpetualMarket(
            binance_api_key=Keys.get_binance_futures_api_key(),
            binance_api_secret=Keys.get_binance_futures_api_secret(),
            order_book_tracker_data_source_type=OrderBookTrackerDataSourceType.EXCHANGE_API,
            user_stream_tracker_data_source_type=UserStreamTrackerDataSourceType.EXCHANGE_API,
            trading_pairs=["ETHUSDT"]
        )
        print("Initializing Binance Perpetual market... this will take about a minute.")
        cls.ev_loop: asyncio.BaseEventLoop = asyncio.get_event_loop()
        cls.clock.add_iterator(cls.market)
        cls.stack: contextlib.ExitStack = contextlib.ExitStack()
        cls._clock = cls.stack.enter_context(cls.clock)
        cls.ev_loop.run_until_complete(cls.wait_till_ready())
        print("Market Ready.")

    @classmethod
    async def wait_till_ready(cls):
        while True:
            now = time.time()
            next_iteration = now // 1.0 + 1
            if cls.market.ready:
                break
            else:
                await cls._clock.run_til(next_iteration)
            await asyncio.sleep(1.0)

    def setUp(self) -> None:
        self.market_logger = EventLogger()
        for event_tag in self.events:
            self.market.add_listener(event_tag, self.market_logger)

    def tearDown(self):
        for event_tag in self.events:
            self.market.remove_listener(event_tag, self.market_logger)
        self.market_logger = None

    @classmethod
    def tearDownClass(cls) -> None:
        cls.stack.close()

    async def run_parallel_async(self, *tasks):
        future: asyncio.Future = safe_ensure_future(safe_gather(*tasks))
        while not future.done():
            now = time.time()
            next_iteration = now // 1.0 + 1
            await self._clock.run_til(next_iteration)
            await asyncio.sleep(1.0)
        return future.result()

    def run_parallel(self, *tasks):
        return self.ev_loop.run_until_complete(self.run_parallel_async(*tasks))

    @unittest.skip("Too Simple, Unnecessary")
    def test_network_status(self):
        network_status: NetworkStatus = self.ev_loop.run_until_complete(self.market.check_network())
        self.assertEqual(NetworkStatus.CONNECTED, network_status)

    # @unittest.skip("")
    def test_buy_and_sell_order_then_cancel_individually(self):
        trading_pair = "ETHUSDT"
        # Create Buy Order
        buy_order_id = self.market.buy(
            trading_pair=trading_pair,
            amount=Decimal(0.01),
            order_type=OrderType.LIMIT,
            price=Decimal(300)
        )
        [order_created_event] = self.run_parallel(self.market_logger.wait_for(BuyOrderCreatedEvent))
        order_created_event: BuyOrderCreatedEvent = order_created_event
        self.assertEqual(buy_order_id, order_created_event.order_id)
        self.assertEqual(trading_pair, order_created_event.trading_pair)
        self.assertEqual(1, len(self.market.in_flight_orders))
        self.assertTrue(buy_order_id in self.market.in_flight_orders)

        # Create Sell Order
        sell_order_id = self.market.sell(
            trading_pair=trading_pair,
            amount=Decimal(0.01),
            order_type=OrderType.LIMIT,
            price=Decimal(500)
        )
        [order_created_event] = self.run_parallel(self.market_logger.wait_for(SellOrderCreatedEvent))
        order_created_event: SellOrderCreatedEvent = order_created_event
        self.assertEqual(sell_order_id, order_created_event.order_id)
        self.assertEqual(trading_pair, order_created_event.trading_pair)
        self.assertEqual(2, len(self.market.in_flight_orders))
        self.assertTrue(sell_order_id in self.market.in_flight_orders)
        self.assertTrue(buy_order_id in self.market.in_flight_orders)

        # Cancel Buy Order
        self.market.cancel(trading_pair, buy_order_id)
        [order_cancelled_event] = self.run_parallel(self.market_logger.wait_for(OrderCancelledEvent))
        order_cancelled_event: OrderCancelledEvent = order_cancelled_event
        self.assertEqual(buy_order_id, order_cancelled_event.order_id)
        self.assertEqual(1, len(self.market.in_flight_orders))
        self.assertTrue(sell_order_id in self.market.in_flight_orders)
        self.assertTrue(buy_order_id not in self.market.in_flight_orders)

        # Cancel Sell Order
        self.market.cancel(trading_pair, sell_order_id)
        [order_cancelled_event] = self.run_parallel(self.market_logger.wait_for(OrderCancelledEvent))
        order_cancelled_event: OrderCancelledEvent = order_cancelled_event
        self.assertEqual(sell_order_id, order_cancelled_event.order_id)
        self.assertEqual(0, len(self.market.in_flight_orders))
        self.assertTrue(sell_order_id not in self.market.in_flight_orders)
        self.assertTrue(buy_order_id not in self.market.in_flight_orders)

    # @unittest.skip("")
    def test_buy_and_sell_order_then_cancel_all(self):
        trading_pair = "ETHUSDT"
        # Create Buy Order
        buy_order_id = self.market.buy(
            trading_pair=trading_pair,
            amount=Decimal(0.01),
            order_type=OrderType.LIMIT,
            price=Decimal(300)
        )
        [order_created_event] = self.run_parallel(self.market_logger.wait_for(BuyOrderCreatedEvent))
        order_created_event: BuyOrderCreatedEvent = order_created_event
        self.assertEqual(buy_order_id, order_created_event.order_id)
        self.assertEqual(trading_pair, order_created_event.trading_pair)
        self.assertEqual(1, len(self.market.in_flight_orders))
        self.assertTrue(buy_order_id in self.market.in_flight_orders)

        # Create Sell Order
        sell_order_id = self.market.sell(
            trading_pair=trading_pair,
            amount=Decimal(0.01),
            order_type=OrderType.LIMIT,
            price=Decimal(500)
        )
        [order_created_event] = self.run_parallel(self.market_logger.wait_for(SellOrderCreatedEvent))
        order_created_event: SellOrderCreatedEvent = order_created_event
        self.assertEqual(sell_order_id, order_created_event.order_id)
        self.assertEqual(trading_pair, order_created_event.trading_pair)
        self.assertEqual(2, len(self.market.in_flight_orders))
        self.assertTrue(sell_order_id in self.market.in_flight_orders)
        self.assertTrue(buy_order_id in self.market.in_flight_orders)

        # Cancel All Orders
        [cancellation_results] = self.run_parallel(self.market.cancel_all(5))
        for cancel_result in cancellation_results:
            self.assertEqual(cancel_result.success, True)

        self.assertEqual(0, len(self.market.in_flight_orders))
        self.assertTrue(sell_order_id not in self.market.in_flight_orders)
        self.assertTrue(buy_order_id not in self.market.in_flight_orders)

    # @unittest.skip("")
    def test_buy_and_sell_order_then_cancel_account_orders(self):
        trading_pair = "ETHUSDT"
        # Create Buy Order
        buy_order_id = self.market.buy(
            trading_pair=trading_pair,
            amount=Decimal(0.01),
            order_type=OrderType.LIMIT,
            price=Decimal(300)
        )
        [order_created_event] = self.run_parallel(self.market_logger.wait_for(BuyOrderCreatedEvent))
        order_created_event: BuyOrderCreatedEvent = order_created_event
        self.assertEqual(buy_order_id, order_created_event.order_id)
        self.assertEqual(trading_pair, order_created_event.trading_pair)
        self.assertEqual(1, len(self.market.in_flight_orders))
        self.assertTrue(buy_order_id in self.market.in_flight_orders)

        # Create Sell Order
        sell_order_id = self.market.sell(
            trading_pair=trading_pair,
            amount=Decimal(0.01),
            order_type=OrderType.LIMIT,
            price=Decimal(500)
        )
        [order_created_event] = self.run_parallel(self.market_logger.wait_for(SellOrderCreatedEvent))
        order_created_event: SellOrderCreatedEvent = order_created_event
        self.assertEqual(sell_order_id, order_created_event.order_id)
        self.assertEqual(trading_pair, order_created_event.trading_pair)
        self.assertEqual(2, len(self.market.in_flight_orders))
        self.assertTrue(sell_order_id in self.market.in_flight_orders)
        self.assertTrue(buy_order_id in self.market.in_flight_orders)

        # Cancel All Open Orders on Account (specified by trading pair)
        self.ev_loop.run_until_complete(safe_ensure_future(self.market.cancel_all_account_orders(trading_pair)))
        self.assertEqual(0, len(self.market.in_flight_orders))
        self.assertTrue(sell_order_id not in self.market.in_flight_orders)
        self.assertTrue(buy_order_id not in self.market.in_flight_orders)


def main():
    logging.getLogger("hummingbot.core.event.event_reporter").setLevel(logging.WARNING)
    unittest.main()


if __name__ == "__main__":
    main()
