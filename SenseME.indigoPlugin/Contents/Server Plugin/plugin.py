#! /usr/bin/env python
# -*- coding: utf-8 -*-

import indigo
import indigoPluginUpdateChecker

import os
import sys
import socket
import re

################################################################################
class Plugin(indigo.PluginBase):
    ########################################
    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        indigo.PluginBase.__init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs)
        self.debug = True

        self.initializing = {}

        self.updater = indigoPluginUpdateChecker.updateChecker(self, 'http://bruce.pennypacker.org/files/PluginVersions/SenseME.html', 7)

    def __del__(self):
        indigo.PluginBase.__del__(self)

    def DebugMsg(self, msg):
        if 'debug' in self.pluginPrefs and self.pluginPrefs['debug']:
            self.debugLog(msg)

    ########################################
    def startup(self):
        self.DebugMsg(u'startup called')

    ########################################
    def shutdown(self):
        self.DebugMsg(u"shutdown called")

    ########################################
    def queryFan(self, fanIP, msg):
        sock = socket.socket()
        sock.settimeout(5)
        sock.connect((fanIP, 31415))

        sock.send(msg)
        status = sock.recv(1024)
        sock.close

        matchObj = re.match('\(.*;([^;]+)\)', status)
        if matchObj:
            self.DebugMsg("query %s returned: %s" % ( msg, matchObj.group(1) ))
            return (matchObj.group(1))
        else:
            self.DebugMsg("fetch %s returned: unknown: %s" % ( msg, status ))
            return False

    ########################################
    def deviceStartComm(self, dev):
        self.DebugMsg("Starting device  %s." % dev.name)

        self.initializing[dev.name] = True

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']

        # Build up a string of messgaes to query the current state of the fan
        msg = "<%s;FAN;SPD;GET;ACTUAL>" % ( fanName )
        res = self.queryFan(fanIP, msg)
        if res:
            dev.updateStateOnServer('fan', (res != '0'))
            dev.updateStateOnServer('speed', int(res))

        msg = "<%s;LIGHT;LEVEL;GET;ACTUAL>" % ( fanName )
        res = self.queryFan(fanIP, msg)
        if res:
            dev.updateStateOnServer('light', (res != '0'))
            dev.updateStateOnServer('brightness', int(res))

        msg = "<%s;FAN;AUTO;GET>" % ( fanName )
        res = self.queryFan(fanIP, msg)
        if res:
            dev.updateStateOnServer('fan_motion', res)

        msg = "<%s;LIGHT;AUTO;GET>" % ( fanName )
        res = self.queryFan(fanIP, msg)
        if res:
            dev.updateStateOnServer('light_motion', res)
        
        del self.initializing[dev.name] 
        
    ########################################
    def deviceStopComm(self, dev):
        self.DebugMsg("Stopping device  %s." % dev.name)

    ########################################
    def runConcurrentThread(self):
        try:
            self.DebugMsg(u"starting runConcurrentThread()")
            hostIP = '0.0.0.0'
            self.DebugMsg("Starting UDP listener")

            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(2)
            sock.bind((hostIP, 31415))

            while True:
                try:
                    data, addr = sock.recvfrom(1024)
                except socket.timeout:
                    self.sleep(0.1)
                    continue

                if "ALL;DEVICE;ID;GET" not in data:
                    self.DebugMsg(u"received %s from %s" % (data, addr))

                matchObj = re.match('\((.*)\)', data)
                if matchObj:
                    cmdStr = matchObj.group(1)
                    params = cmdStr.split(';')
                    fanName = params[0]

                    for dev in indigo.devices.iter("self"):
                        if not dev.enabled or not dev.configured:
                            continue
                        if dev.pluginProps['fanName'] != fanName:
                            continue

                        if dev.name in self.initializing and self.initializing[dev.name]:
                            self.DebugMsg(u"initializing.  ignoring %s" % (data))
                            continue

                        if ';LIGHT;LEVEL;ACTUAL;' in cmdStr:
                            dev.updateStateOnServer('brightness', int(params[4]))
                        elif ';FAN;SPD;CURR;' in cmdStr:
                            dev.updateStateOnServer('speed', int(params[4]))
                        elif ';FAN;AUTO;' in cmdStr:
                            dev.updateStateOnServer('fan_motion', params[3])
                        elif ';LIGHT;AUTO;' in cmdStr:
                            dev.updateStateOnServer('light_motion', params[3])
                        elif ';LIGHT;PWR;' in cmdStr:
                            dev.updateStateOnServer('light', (params[3] == 'ON'))
                        elif ';FAN;PWR;' in cmdStr:
                            dev.updateStateOnServer('fan', (params[3] == 'ON'))


                self.sleep(0.1)

        except self.StopThread:
            pass    # Optionally catch the StopThread exception and do any needed cleanup.

    ########################################
    def validateDeviceConfigUi(self, valuesDict, typeId, devId):
        return (True, valuesDict)

    ########################################
    def setFanLightOn(self, action):
        dev = indigo.devices[action.deviceId]

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;LIGHT;PWR;ON>" % ( fanName )

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setFanLightOff(self, action):
        dev = indigo.devices[action.deviceId]

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;LIGHT;PWR;OFF>" % ( fanName )

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setFanLightBrightness(self, action):
        dev = indigo.devices[action.deviceId]

        lightLevel = action.props.get("lightLevel")

        self.DebugMsg(u"set brightness to %s" % (lightLevel))

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;LIGHT;LEVEL;SET;%s>" % ( fanName, lightLevel)

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def validateActionConfigUi(self, valuesDict, typeId, actionId):
        self.DebugMsg(u"validating config for %s:%s" % (actionId, typeId))
        if typeId == 'fanLightBrightness':
            try:
                i = int(valuesDict['lightLevel'])
                if i < 0 or i > 16:
                    errorDict = indigo.Dict()
                    errorDict["lightLevel"] = "Brightness must be an integer between 0 and 16"
                    return (False, valuesDict, errorDict)
                else:
                    return True
            except:
                errorDict = indigo.Dict()
                errorDict["lightLevel"] = "Brightness must be an integer between 0 and 16"
                return (False, valuesDict, errorDict)
        elif typeId == 'fanSpeed':
            try:
                i = int(valuesDict['speed'])
                if i < 0 or i > 6:
                    errorDict = indigo.Dict()
                    errorDict["speed"] = "Speed must be an integer between 0 and 6"
                    return (False, valuesDict, errorDict)
                else:
                    return True
            except:
                errorDict = indigo.Dict()
                errorDict["speed"] = "Speed must be an integer between 0 and 6"
                return (False, valuesDict, errorDict)

        return True

    ########################################
    def setFanRawCommand(self, action):
        dev = indigo.devices[action.deviceId]
        fanIP = dev.pluginProps['fanIP']
        cmd = action.props.get("cmd")
        if cmd:
            self.DebugMsg(u"sending raw command %s to %s" % (cmd, fanIP))
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(cmd, (fanIP, 31415))

    ########################################
    def setFanSpeed(self, action):
        dev = indigo.devices[action.deviceId]

        speed = action.props.get("speed")

        self.DebugMsg(u"set speed to %s" % (speed))

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;FAN;SPD;SET;%s>" % ( fanName, speed )

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setFanOn(self, action):
        dev = indigo.devices[action.deviceId]

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;FAN;PWR;ON>" % ( fanName )

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setFanOff(self, action):
        dev = indigo.devices[action.deviceId]

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;FAN;PWR;OFF>" % ( fanName )

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setFanMotionSensorOff(self, action):
        dev = indigo.devices[action.deviceId]

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;FAN;AUTO;OFF>" % ( fanName )

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setFanMotionSensorOn(self, action):
        dev = indigo.devices[action.deviceId]

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;FAN;AUTO;ON>" % ( fanName )

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setLightMotionSensorOff(self, action):
        dev = indigo.devices[action.deviceId]

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;LIGHT;AUTO;OFF>" % ( fanName )

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setLightMotionSensorOn(self, action):
        dev = indigo.devices[action.deviceId]

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;LIGHT;AUTO;ON>" % ( fanName )

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

