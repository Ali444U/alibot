import logging
from enum import Enum

import numpy as np
import pandas as pd
from gym import spaces
from pandas import DataFrame

from freqtrade.freqai.RL.BaseEnvironment import BaseEnvironment, Positions


logger = logging.getLogger(__name__)


class Actions(Enum):
    Neutral = 0
    Long_enter = 1
    Long_exit = 2
    Short_enter = 3
    Short_exit = 4


def mean_over_std(x):
    std = np.std(x, ddof=1)
    mean = np.mean(x)
    return mean / std if std > 0 else 0


class Base5ActionRLEnv(BaseEnvironment):
    """
    Base class for a 5 action environment
    """

    def set_action_space(self):
        self.action_space = spaces.Discrete(len(Actions))

    def reset(self):

        self._done = False

        if self.starting_point is True:
            self._position_history = (self._start_tick * [None]) + [self._position]
        else:
            self._position_history = (self.window_size * [None]) + [self._position]

        self._current_tick = self._start_tick
        self._last_trade_tick = None
        self._position = Positions.Neutral

        self.total_reward = 0.
        self._total_profit = 1.  # unit
        self.history = {}
        self.trade_history = []
        self.portfolio_log_returns = np.zeros(len(self.prices))

        self._profits = [(self._start_tick, 1)]
        self.close_trade_profit = []
        self._total_unrealized_profit = 1

        return self._get_observation()

    def step(self, action: int):
        """
        Logic for a single step (incrementing one candle in time)
        by the agent
        :param: action: int = the action type that the agent plans
            to take for the current step.
        :returns:
            observation = current state of environment
            step_reward = the reward from `calculate_reward()`
            _done = if the agent "died" or if the candles finished
            info = dict passed back to openai gym lib
        """
        self._done = False
        self._current_tick += 1

        if self._current_tick == self._end_tick:
            self._done = True

        self.update_portfolio_log_returns(action)

        self._update_unrealized_total_profit()
        step_reward = self.calculate_reward(action)
        self.total_reward += step_reward

        trade_type = None
        if self.is_tradesignal(action):
            """
            Action: Neutral, position: Long ->  Close Long
            Action: Neutral, position: Short -> Close Short

            Action: Long, position: Neutral -> Open Long
            Action: Long, position: Short -> Close Short and Open Long

            Action: Short, position: Neutral -> Open Short
            Action: Short, position: Long -> Close Long and Open Short
            """

            if action == Actions.Neutral.value:
                self._position = Positions.Neutral
                trade_type = "neutral"
                self._last_trade_tick = None
            elif action == Actions.Long_enter.value:
                self._position = Positions.Long
                trade_type = "long"
                self._last_trade_tick = self._current_tick
            elif action == Actions.Short_enter.value:
                self._position = Positions.Short
                trade_type = "short"
                self._last_trade_tick = self._current_tick
            elif action == Actions.Long_exit.value:
                self._update_total_profit()
                self._position = Positions.Neutral
                trade_type = "neutral"
                self._last_trade_tick = None
            elif action == Actions.Short_exit.value:
                self._update_total_profit()
                self._position = Positions.Neutral
                trade_type = "neutral"
                self._last_trade_tick = None
            else:
                print("case not defined")

            if trade_type is not None:
                self.trade_history.append(
                    {'price': self.current_price(), 'index': self._current_tick,
                     'type': trade_type})

        if (self._total_profit < self.max_drawdown or
                self._total_unrealized_profit < self.max_drawdown):
            self._done = True

        self._position_history.append(self._position)

        info = dict(
            tick=self._current_tick,
            total_reward=self.total_reward,
            total_profit=self._total_profit,
            position=self._position.value
        )

        observation = self._get_observation()

        self._update_history(info)

        return observation, step_reward, self._done, info

    def _get_observation(self):
        features_window = self.signal_features[(
            self._current_tick - self.window_size):self._current_tick]
        features_and_state = DataFrame(np.zeros((len(features_window), 3)),
                                       columns=['current_profit_pct', 'position', 'trade_duration'],
                                       index=features_window.index)

        features_and_state['current_profit_pct'] = self.get_unrealized_profit()
        features_and_state['position'] = self._position.value
        features_and_state['trade_duration'] = self.get_trade_duration()
        features_and_state = pd.concat([features_window, features_and_state], axis=1)
        return features_and_state

    def get_trade_duration(self):
        if self._last_trade_tick is None:
            return 0
        else:
            return self._current_tick - self._last_trade_tick

    def is_tradesignal(self, action: int):
        # trade signal
        """
        Determine if the signal is a trade signal
        e.g.: agent wants a Actions.Long_exit while it is in a Positions.short
        """
        return not ((action == Actions.Neutral.value and self._position == Positions.Neutral) or
                    (action == Actions.Neutral.value and self._position == Positions.Short) or
                    (action == Actions.Neutral.value and self._position == Positions.Long) or
                    (action == Actions.Short_enter.value and self._position == Positions.Short) or
                    (action == Actions.Short_enter.value and self._position == Positions.Long) or
                    (action == Actions.Short_exit.value and self._position == Positions.Long) or
                    (action == Actions.Short_exit.value and self._position == Positions.Neutral) or
                    (action == Actions.Long_enter.value and self._position == Positions.Long) or
                    (action == Actions.Long_enter.value and self._position == Positions.Short) or
                    (action == Actions.Long_exit.value and self._position == Positions.Short) or
                    (action == Actions.Long_exit.value and self._position == Positions.Neutral))

    def _is_valid(self, action: int):
        # trade signal
        """
        Determine if the signal is valid.
        e.g.: agent wants a Actions.Long_exit while it is in a Positions.short
        """
        # Agent should only try to exit if it is in position
        if action in (Actions.Short_exit.value, Actions.Long_exit.value):
            if self._position not in (Positions.Short, Positions.Long):
                return False

        # Agent should only try to enter if it is not in position
        if action in (Actions.Short_enter.value, Actions.Long_enter.value):
            if self._position != Positions.Neutral:
                return False

        return True
