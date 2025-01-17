#!/usr/bin/env python3
#
#  tcl_env.py
#  TCL environment for RL algorithms
#
# Author: Taha Nakabi
import random
import numpy as np
import pandas as pd
from sklearn.preprocessing import MinMaxScaler
from matplotlib import pyplot
import gym
# Trying out if this works for others. from gym import spaces had some issues
import gym.spaces as spaces

import math

# Default parameters for 
# default TCL environment.
# From Taha's code
DEFAULT_ITERATIONS = 24
DEFAULT_NUM_TCLS = 100
DEFAULT_NUM_LOADS = 150
# Load up default prices and 
# temperatures (from Taha's CSV)
default_data = np.load("default_price_and_temperatures.npy")
DEFAULT_PRICES = default_data[:,0]
DEFAULT_TEMPERATURS = default_data[:,1]
BASE_LOAD = np.array([2.0,2.0,2.0,2.0,3.4,4.0,6.0,5.5,6.0,5.5,4.0,3.3,4.1,3.3,4.1,2.0,2.0,2.0,2.0,2.0,2.0,2.0,2.0,2.0])
# https://austinenergy.com/ae/residential/rates/residential-electric-rates-and-line-items
PRICE_TIERS = np.array([2.8,5.8,7.8,9.3,10.81])

HIGH_PRICE_PENALTY = 2.0
FIXED_COST = 0
QUADRATIC_PRICE = .025

# Default Tmin and Tmax in TCLs
TCL_TMIN = 19
TCL_TMAX = 25
TCL_PENALTY=0.1
MAX_R = 1
MAX_GENERATION = 120
SOCS_RENDER=[]
LOADS_RENDER =[]
BATTERY_RENDER = []
PRICE_RENDER = []
ENERGY_SOLD_RENDER = []
ENERGY_BOUGHT_RENDER = []
GRID_PRICES_RENDER = []
ENERGY_GENERATED_RENDER = []
TCL_CONTROL_RENDER=[]
TCL_CONSUMPTION_RENDER=[]




class TCL:
    """ 
    Simulates an invidual TCL
    """
    def __init__(self, ca, cm, q, P, Tmin=TCL_TMIN, Tmax=TCL_TMAX):
        self.ca = ca # thermal mass of air
        self.cm = cm # termal mass of building materials
        self.q = q # internal hearing of the building
        self.P = P # what's this? is it the nominal power of the TLC?
        self.Tmin = Tmin
        self.Tmax = Tmax

        # Added for clarity
        self.u = 0

    def set_T(self, T, Tm):
        self.T = T
        self.Tm = Tm

    def control(self, ui=0):
        # control TCL using u with respect to the backup controller
        if self.T < self.Tmin:
            # if the temperature is then than the minimun set the backup controller to on
            self.u = 1
        elif self.Tmin<self.T<self.Tmax:
            # if the temperature is within the boundaries then use u_i control
            self.u = ui
        else:
            # if temperature is over the boundary set the backup controller to off
            self.u = 0

    def update_state(self, T0):
        # update the indoor and mass temperatures according to (22)
        for _ in range(5):
            self.T +=  self.ca * (T0 - self.T) + self.cm * (self.Tm - self.T) + self.P * self.u +self.q
            self.Tm += self.cm*(self.T - self.Tm)
            if self.T>=self.Tmax:
                 break

    """ 
    @property allows us to write "tcl.SoC", and it will
    run this function to get the value
    """
    @property
    def SoC(self):
        return (self.T-self.Tmin)/(self.Tmax-self.Tmin)

class Battery:
    # Simulates the battery system of the microGrid
    def __init__(self, capacity, useD, dissipation, lossC, rateC, maxDD, chargeE, tmax):
        self.capacity = capacity #full charge battery capacity
        self.useD = useD # useful discharge coefficient
        self.dissipation = dissipation # dissipation coefficient of the battery
        self.lossC = lossC #charge loss
        self.rateC = rateC #charging rate
        self.maxDD = maxDD #maximum power that the battery can deliver per timestep
        self.tmax= tmax #maxmum charging time
        self.chargeE = chargeE #Energy given to the battery to charge
        self.RC = 0 #remaining capacity
        self.ct = 0 #Charging step

    def charge(self, E):
        empty = self.capacity-self.RC
        if empty <= 0:
            return E
        else:
            self.RC += self.rateC*E
            leftover = self.RC - self.capacity
            self.RC = min(self.capacity,self.RC)
            return max(leftover,0)


    def supply(self, E):
        remaining = self.RC
        self.RC-= E*self.useD
        self.RC = max(self.RC,0)
        return min(E, remaining)

    def dissipate(self):
        self.RC = self.RC * math.exp(- self.dissipation)

    @property
    def SoC(self):
        return self.RC/self.capacity

class Grid:
    def __init__(self):
        '''
        The transactions between the main grid and the microgrid happen in 
        real-time using the up-regulation and down-regulation market prices.

        in this case the data is from price data from the balancing electricity market in Finland.
        Referneced in the paper as [49]
        '''
        down_reg_df=pd.read_csv("down_regulation.csv")
        up_reg_df = pd.read_csv("up_regulation.csv")
        down_reg = np.array(down_reg_df.iloc[:,-1])/10
        up_reg = np.array(up_reg_df.iloc[:, -1])/10
        self.buy_prices = down_reg
        self.sell_prices = up_reg
        self.time = 0

    def sell(self, E):
        return self.sell_prices[self.time]*E 

    def buy(self,E):
        return -self.buy_prices[self.time]*E - QUADRATIC_PRICE*E**2 - FIXED_COST
    #
    # def get_price(self,time):
    #     return self.prices[time]

    def set_time(self,time):
        self.time = time


class Generation:
    def __init__(self, max_capacity=None):
        # Data from a wind farm in Finland referenced in paper as [48]
        power_df = pd.read_csv("wind_generation.csv")
        self.power = np.array(power_df.iloc[:,-1])
        self.max_capacity = np.max(self.power)

    def current_generation(self,time):
        # We consider that we have 2 sources of power a constant source and a variable source
        # returns power generated at a given time according to the wind energy production data
        return  self.power[time]


class Load:
    def __init__(self, price_sens, base_load, max_v_load):
        self.price_sens = price_sens
        self.base_load = base_load
        self.max_v_load = max_v_load
        self.response = 0
        # Duda: donde estan las cargas de pasos anteriores?

    def react(self, price_tier):
        """
        load reacts to the price tier by lowering the sensitivity
        if the price tier is higher than 2 unless the sensitivity
        is already too small (smaller than 0.1), and updates the
        response to be senstivity * (price_tier - 2)
        """
        self.response = self.price_sens*(price_tier-2) # shoudn't this be multiplied by the base load?
        if self.response > 0 and self.price_sens > 0.1:
            self.price_sens-= 0.1

    def load(self, time_day):
        """ given a moment of time in the day this computes the load of the household
        L_base - shifted load (which is defined in the paper section 2.5, eq 7)
        """
        # print(self.response)
        return max(self.base_load[time_day] - self.max_v_load*self.response,0)  #this is the electric load of the household L_t^i



class TCLEnv(gym.Env):
    def __init__(self, **kwargs):
        """
        Arguments:
            iterations: Number of iterations to run
            num_tcls: Number of TCLs to create in cluster
            prices: Numpy 1D array of prices at different times
            temperatures : Numpy 1D array of temperatures at different times
        """

        # Get number of iterations and TCLs from the 
        # parameters (we have to define it through kwargs because 
        # of how Gym works...)
        self.iterations = kwargs.get("iterations", DEFAULT_ITERATIONS)
        self.num_tcls = kwargs.get("num_tcls", DEFAULT_NUM_TCLS)
        self.num_loads = kwargs.get("num_loads", DEFAULT_NUM_LOADS)
        self.prices = kwargs.get("prices", DEFAULT_PRICES)
        self.temperatures = kwargs.get("temperatures", DEFAULT_TEMPERATURS)
        self.base_load = kwargs.get("base_load", BASE_LOAD)
        self.price_tiers = kwargs.get("price_tiers", PRICE_TIERS)

        # The current day: pick randomly
        self.day = random.randint(0,10)
        # self.day = 55
        # The current timestep
        self.time_step = 0



        # The cluster of TCLs to be controlled.
        # These will be created in reset()
        self.tcls_parameters = []
        self.tcls = []
        # The cluster of loads.
        # These will be created in reset()
        self.loads_parameters = []
        self.loads = []

        self.generation = Generation(MAX_GENERATION)
        self.grid = Grid()

        for i in range(self.num_tcls):
            self.tcls_parameters.append(self._create_tcl_parameters())

        for i in range(self.num_loads):
            self.loads_parameters.append(self._create_load_parameters())

        self.action_space = spaces.Box(low=0, high=1, dtype=np.float32,
                    shape=(13,))
        
        # Observations: A vector of TCLs SoCs + loads +battery soc+ power generation + price + temperature + time of day
        self.observation_space = spaces.Box(low=-100, high=100, dtype=np.float32, 
                    shape=(self.num_tcls  + 6,))


    def _create_tcl_parameters(self):
        """
                Initialize one TCL randomly with given T_0,
                and return it. Copy/paste from Taha's code
                """
        # Hardcoded initialization values to create
        # bunch of different TCLs
        ca = random.normalvariate(0.004, 0.0008) # termal mass of air
        cm = random.normalvariate(0.2, 0.004) # termal mass of building materials
        q = random.normalvariate(0, 0.01) # internal heating of the building
        P = random.normalvariate(1.5, 0.01) # what is this?
        return [ca,cm,q,P]

    def _create_tcl(self,ca ,cm ,q ,P, initial_temperature):
        tcl= TCL(ca,cm,q,P)
        tcl.set_T(initial_temperature,initial_temperature)
        return tcl
    def _create_load_parameters(self):

        """
        Initialize one load randomly,
        and return it.
        """
        # Hardcoded initialization values to create
        # bunch of different loads

        price_sensitivity= random.normalvariate(0.5, 0.3)
        max_v_load = random.normalvariate(3.0, 1.0)
        return [price_sensitivity,max_v_load]

    def _create_load(self,price_sensitivity,max_v_load):
        load = Load(price_sensitivity,base_load=self.base_load, max_v_load=max_v_load)
        return load

    def _create_battery(self):
        """
        Initialize one battery
        """
        battery = Battery(capacity = 400.0, useD=0.9, dissipation=0.001, lossC=0.15, rateC=0.9, maxDD=10, chargeE=10, tmax=5)
        return battery

    def _build_state(self):
        """ 
        Return current state representation as one vector.
        Returns:
            state: 1D state vector, containing state-of-charges of all TCLs, Loads, current battery soc, current power generation,
                   current temperature, current price and current time (hour) of day
        """
        # SoCs of all TCLs binned + current temperature + current price + time of day (hour)
        socs = np.array([tcl.SoC for tcl in self.tcls])
        # Scaling between -1 and 1
        socs = (socs+np.ones(shape=socs.shape)*4)/(1+4)

        # loads = np.array([l.load(self.time_step) for l in self.loads])
        loads = sum([l.load(self.time_step) for l in self.loads])
        # Scaling loads
        loads = (loads-(min(BASE_LOAD)+2)*DEFAULT_NUM_LOADS)/((max(BASE_LOAD)+4-min(BASE_LOAD)-2)*DEFAULT_NUM_LOADS)
        # print(loads)
        current_generation = self.generation.current_generation(self.day+self.time_step)
        current_generation /= self.generation.max_capacity
        temperature = self.temperatures[self.day+self.time_step]
        temperature = (temperature-min(self.temperatures))/(max(self.temperatures)-min(self.temperatures))
        price = self.grid.buy_prices[self.day+self.time_step]
        price = (price - min(self.grid.buy_prices)) / (max(self.grid.buy_prices) - min(self.grid.buy_prices))
        time_step = self.time_step/24
        state = np.concatenate((socs, [loads,self.battery.SoC, current_generation,
                         temperature,
                         price,
                         time_step ]))
        return state

    def _build_info(self):
        """
        Return dictionary of misc. infos to be given per state.
        Here this means providing forecasts of future
        prices and temperatures (next 24h)
        """
        temp_forecast = np.array(self.temperatures[self.time_step+1:self.time_step+25])
        price_forecast = np.array(self.prices[self.time_step+1:self.time_step+25])
        return {"temperature_forecast": temp_forecast, 
                "price_forecast": price_forecast,
                "forecast_times": np.arange(0,self.iterations)}

    
    def _compute_tcl_power(self):
        """
        Return the total power consumption of all TCLs
        """
        return sum([tcl.u*tcl.P for tcl in self.tcls])

    def step(self, action):
        """ 
        Arguments:
            action: A scalar float. 
        
        Returns:
            state: Current state
            reward: How much reward was obtained on last action
            terminal: Boolean on if the game ended (maximum number of iterations)
            info: None (not used here)
        """

        self.grid.set_time(self.day+self.time_step)
        reward = 0
        # Update state of TCLs according to action, this action is composed of a vector of 4
        # [tlc_action, price_action, energy_deficiency_action, energy_excess_action]

        tcl_action = action[0]
        price_action = action[1] # this action is an int from 0 to 5
        energy_deficiency_action = action[2] # boolean 1 to store and sell excess, 0 to sell all of it
        energy_excess_action = action[3] # boolean that decides whether to use up the energy in the battery before buying or not
        # Get the energy generated by the DER (Distributed energy resource model)
        available_energy = self.generation.current_generation(self.day+self.time_step)
        # Energy rate
        # self.eRate = available_energy/self.generation.max_capacity

        # print("Generated power: ", available_energy)
        # We implement the pricing action and we calculate the total load in response to the price
        for load in self.loads:
            load.react(price_action) # this lowers the sensitivity if the price tier is too high
        total_loads = sum([l.load(self.time_step) for l in self.loads]) # computes sum of household loads
        # print("Total loads",total_loads)
        # We fulfilled the load with the available energy.
        available_energy -= total_loads # subtract the loads from the available energy
        
        # We calculate the return based on the sale price.
        self.sale_price = self.price_tiers[price_action] # set sale prices with the pricing action (from global variable PRICE_TIERS)

        # We increment the reward by the amount of return (this is according to 3.3 eq 25 first term)
        # Division by 100 to transform from cents to euros
        reward += total_loads*self.sale_price/100 # first term of eq 25
        # Penalty of charging too high prices
        self.high_price += price_action # initialy set to 0, now it's equal to the price action
        # Distributing the energy according to priority
        sortedTCLs = sorted(self.tcls, key=lambda x: x.SoC) # sort the TLCs by their SoC
        # print(tcl_action)
        control = tcl_action*50.0
        self.control = control # = tlc_action * 50.0
        for tcl in sortedTCLs:
            # for each TLC if the tlc_action is bigger than 0
            if control>0:
                # then use the control 1 (turn the set the action to on if temperature allows it)
                tcl.control(1)
                # substract from the control P times the final control u_b,t of the tlc
                control-= tcl.P * tcl.u
            else:
                # if the control action is less or equal to 0
                # set the action to off if the temperature allows it
                tcl.control(0)
            # update the temperature
            tcl.update_state(self.temperatures[self.day+self.time_step])
            # if tcl.SoC >1 :
            #     reward -= abs((tcl.SoC-1) * reward*TCL_PENALTY)
            # if  tcl.SoC<0:
            #     reward += tcl.SoC * abs(reward*TCL_PENALTY)
        # substract the power used in the tcls from the available energy
        available_energy -= self._compute_tcl_power()
        # control_error = self.sale_price*(self.control-self._compute_tcl_power())**2
        # add to the reward the sum of the tlcs's nominal power times the control all of this times the generation cost
        # This line is what the second term of eq 25 gives: 
        reward += self._compute_tcl_power()*self.sale_price/100
        
        if available_energy>0:
            # if there's still available energy left check the energy_excess_action   
            if energy_excess_action:
                # if energy_excess_action == 1
                # then charge the batery with the remaining energy
                available_energy = self.battery.charge(available_energy)
                # and what's left after that it's sold and added to the reward
                # this is according to the third term of eq 25
                reward += self.grid.sell(available_energy)/100
            else:
                # if energy_exess_action == 0
                # sell al the energy available and add to reward acording to the third term of eq 25
                reward += self.grid.sell(available_energy)/100
            # keep track of energy sold and bought
            self.energy_sold =  available_energy
            self.energy_bought = 0

        else:
            # if there's no energy left check the energy deficiency action
            if energy_deficiency_action:
                # if the action == 1 get energy from the battery before buying
                available_energy += self.battery.supply(-available_energy)
            # update the energy bought
            self.energy_bought = -available_energy
            # buy the energy needed to break even
            # add to the reward the negative energy brought with some quadratic cost
            # DUDA: can't find this cuadratic cost on the paper
            reward += self.grid.buy(self.energy_bought)/100
            # update the energy sold
            self.energy_sold = 0 

        # Proceed to next timestep.
        self.time_step += 1
        # Build up the representation of the current state (in the next timestep)
        state = self._build_state() # returns the state representation as a vector of
        # all the SoCs concatenated with [loads, batery SoC, current_generation, temperature, price, time_step]

        # check if it's done, 1 if done, if not is 0.
        terminal = self.time_step == self.iterations-1 
        # DUDA: if the price action is bigger than twice the iterations ( i don't know why this is)
        if self.high_price > 4 * self.iterations / 2:
            # Penalize high prices
            # DUDA: can't find this on the paper
            reward -= abs(reward * HIGH_PRICE_PENALTY * (self.high_price - 4 * self.iterations / 2))
        if terminal:
            # reward if battery is charged by the end
            reward += abs(reward*self.battery.SoC / 4)
        info = self._build_info()
        # consistent return with gym: [state, reward, is_done, info]
        return state, reward, terminal, info

    def reset(self):
        """
        Create new TCLs, and return initial state.
        Note: Overrides previous TCLs
        """
        self.day = random.randint(0,10)
        # self.day = 5
        print("Day:",self.day)
        self.time_step = 0
        self.battery = self._create_battery()
        self.energy_sold = 0
        self.energy_bought = 0
        self.energy_generated = 0
        self.control=0
        self.sale_price = PRICE_TIERS[2]
        self.high_price = 0
        self.tcls.clear()
        initial_tcl_temperature = random.normalvariate(12, 5)

        for i in range(self.num_tcls):
            parameters = self.tcls_parameters[i]

            self.tcls.append(self._create_tcl(parameters[0],parameters[1],parameters[2],parameters[3],initial_tcl_temperature))

        self.loads.clear()
        for i in range(self.num_loads):
            parameters = self.loads_parameters[i]
            self.loads.append(self._create_load(parameters[0],parameters[1]))
        self.battery = self._create_battery()
        return self._build_state()

    def render(self, s):
        SOCS_RENDER.append([tcl.SoC for tcl in self.tcls])
        LOADS_RENDER.append([l.load(self.time_step) for l in self.loads])
        PRICE_RENDER.append(self.sale_price)
        BATTERY_RENDER.append(self.battery.SoC)
        ENERGY_GENERATED_RENDER.append(self.generation.current_generation(self.day+self.time_step))
        ENERGY_SOLD_RENDER.append(self.energy_sold)
        ENERGY_BOUGHT_RENDER.append(self.energy_bought)
        GRID_PRICES_RENDER.append(self.grid.buy_prices[self.day+self.time_step])
        TCL_CONTROL_RENDER.append(self.control)
        TCL_CONSUMPTION_RENDER.append(self._compute_tcl_power())
        if self.time_step==self.iterations-1:
            fig=pyplot.figure()
            ax1 = fig.add_subplot(3,3,1)
            ax1.boxplot(np.array(SOCS_RENDER).T)
            ax1.set_title("TCLs SOCs")
            ax1.set_xlabel("Time (h)")
            ax1.set_ylabel("SOC")

            ax2 = fig.add_subplot(3, 3, 2)
            ax2.boxplot(np.array(LOADS_RENDER).T)
            ax2.set_title("LOADS")
            ax2.set_xlabel("Time (h)")
            ax2.set_ylabel("HOURLY LOADS")

            ax3 = fig.add_subplot(3, 3, 3)
            ax3.plot(PRICE_RENDER)
            ax3.set_title("SALE PRICES")
            ax3.set_xlabel("Time (h)")
            ax3.set_ylabel("HOURLY PRICES")

            ax4 = fig.add_subplot(3, 3, 4)
            ax4.plot(np.array(BATTERY_RENDER))
            ax4.set_title("BATTERY SOC")
            ax4.set_xlabel("Time (h)")
            ax4.set_ylabel("BATTERY SOC")

            ax4 = fig.add_subplot(3, 3, 5)
            ax4.plot(np.array(ENERGY_GENERATED_RENDER))
            ax4.set_title("ENERGY_GENERATED")
            ax4.set_xlabel("Time (h)")
            ax4.set_ylabel("ENERGY_GENERATED")

            ax4 = fig.add_subplot(3, 3, 6)
            ax4.plot(np.array(ENERGY_SOLD_RENDER))
            ax4.set_title("ENERGY_SOLD")
            ax4.set_xlabel("Time (h)")
            ax4.set_ylabel("ENERGY_SOLD")

            ax4 = fig.add_subplot(3, 3, 7)
            ax4.plot(np.array(ENERGY_BOUGHT_RENDER))
            ax4.set_title("ENERGY_BOUGHT")
            ax4.set_xlabel("Time (h)")
            ax4.set_ylabel("ENERGY_BOUGHT")

            ax4 = fig.add_subplot(3, 3, 8)
            ax4.plot(np.array(GRID_PRICES_RENDER))
            ax4.set_title("GRID_PRICES")
            ax4.set_xlabel("Time (h)")
            ax4.set_ylabel("GRID_PRICES_RENDER")

            ax4 = fig.add_subplot(3, 3, 9)
            ax4.bar(x=np.array(np.arange(self.iterations)),height=TCL_CONTROL_RENDER,width=0.2)
            ax4.bar(x=np.array(np.arange(self.iterations))+0.2,height=TCL_CONSUMPTION_RENDER,width=0.2)
            ax4.set_title("TCL_CONTROL VS TCL_CONSUMPTION")
            ax4.set_xlabel("Time (h)")
            ax4.set_ylabel("kW")

            pyplot.show()

            SOCS_RENDER.clear()
            LOADS_RENDER.clear()
            PRICE_RENDER.clear()
            BATTERY_RENDER.clear()
            GRID_PRICES_RENDER.clear()
            ENERGY_BOUGHT_RENDER.clear()
            ENERGY_SOLD_RENDER.clear()
            ENERGY_GENERATED_RENDER.clear()
            TCL_CONTROL_RENDER.clear()
            TCL_CONSUMPTION_RENDER.clear()


    def close(self):
        """ 
        Nothing to be done here, but has to be defined 
        """
        return

    def seed(self, seed):
        """
        Set the random seed for consistent experiments
        """
        random.seed(seed)
        np.random.seed(seed)
        
if __name__ == '__main__':
    # Testing the environment
    from matplotlib import pyplot
    from tqdm import tqdm
    env = TCLEnv()
    env.seed(1)
    #
    states = []
    rewards = []
    state = env.reset()
    states.append(state)
    actions = []
    #
    for i in tqdm(range(100)):
        action = env.action_space.sample()
        # print(action)
        actions.append(action)
        state, reward, terminal, _ = env.step(action)
        print(reward)
        states.append(state)
        rewards.append(reward)
        if terminal:
            break

    # Plot the TCL SoCs 
    states = np.array(rewards)
    pyplot.plot(rewards)
    pyplot.title("rewards")
    pyplot.xlabel("Time")
    pyplot.ylabel("rewards")
    pyplot.show()

    # battery = Battery(capacity = 75.0, useD = 0.8, dissipation = 0.001, lossC = 0.15, rateC = 0.5, maxDD = 10, chargeE=10, tmax = 5)
    # RCs = []
    # for itr in range(5):
    #     RCs.append(battery.SoC)
    #     battery.charge()
    # for itr in range(3):
    #     RCs.append(battery.SoC)
    #     battery.dissipate()
    # for itr in range(5):
    #     RCs.append(battery.SoC)
    #     battery.supply(5)
    # for itr in range(5):
    #     RCs.append(battery.SoC)
    #     battery.charge()
    # for itr in range(10):
    #     RCs.append(battery.SoC)
    #     battery.supply(20)
    #
    # pyplot.plot(RCs)
    # pyplot.show()
