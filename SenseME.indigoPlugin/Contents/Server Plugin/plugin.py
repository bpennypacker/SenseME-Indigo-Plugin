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
        self.MAC = {}
        self.fan_level = {}
        self.light_level = {}

        self.fan = {}
        self.fan_auto = {}
        self.light = {}
        self.light_auto = {}

        self.updater = indigoPluginUpdateChecker.updateChecker(self, 'http://bruce.pennypacker.org/files/PluginVersions/SenseME.html', 7)

    ########################################
    def __del__(self):
        indigo.PluginBase.__del__(self)

    ########################################
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
    def updateStatusString(self, dev):
        if self.light_level[dev.id] == '0':
            l = 'off'
        else:
            l = 'on'

        if self.fan_level[dev.id] == '0':
            f = 'off'
        else:
            f = 'on'

        if 'debug' in self.pluginPrefs and self.pluginPrefs['debug']:
          s = "%s / %s (%s, %s)" % (f, l, self.fan_level[dev.id], self.light_level[dev.id])
        else:
          s = "%s / %s" % (f, l)

        dev.updateStateOnServer('statusString', s)

        if self.fan_level[dev.id] == '0':
            dev.updateStateImageOnServer(indigo.kStateImageSel.FanOff) 
        elif self.fan_level[dev.id] in [ '1', '2' ]:
            dev.updateStateImageOnServer(indigo.kStateImageSel.FanLow) 
        elif self.fan_level[dev.id] in [ '3', '4' ]:
            dev.updateStateImageOnServer(indigo.kStateImageSel.FanMedium) 
        elif self.fan_level[dev.id] in [ '5', '6' ]:
            dev.updateStateImageOnServer(indigo.kStateImageSel.FanHigh) 
        else:
            dev.updateStateImageOnServer(indigo.kStateImageSel.Error) 
        

    ########################################
    def deviceStartComm(self, dev):
        self.DebugMsg("Starting device  %s." % dev.name)

        self.initializing[dev.id] = True
        self.MAC[dev.id] = '';
        self.light_level[dev.id] = '';
        self.fan_level[dev.id] = '';

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']

        msg = "<%s;LIGHT;PWR;GET>" % ( fanName )
        res = self.queryFan(fanIP, msg)
        if res:
            self.light[dev.id] = res
            dev.updateStateOnServer('light', (res == 'ON'))

        msg = "<%s;FAN;PWR;GET>" % ( fanName )
        res = self.queryFan(fanIP, msg)
        if res:
            self.fan[dev.id] = res
            dev.updateStateOnServer('fan', (res == 'ON'))

        msg = "<%s;FAN;SPD;GET;ACTUAL>" % ( fanName )
        res = self.queryFan(fanIP, msg)
        if res:
            self.fan_level[dev.id] = res
            dev.updateStateOnServer('speed', int(res))

        msg = "<%s;LIGHT;LEVEL;GET;ACTUAL>" % ( fanName )
        res = self.queryFan(fanIP, msg)
        if res:
            self.light_level[dev.id] = res
            if (res != 'NOT PRESENT'):
                dev.updateStateOnServer('brightness', int(res))
            else:
                dev.updateStateOnServer('brightness', 0)

        msg = "<%s;FAN;AUTO;GET>" % ( fanName )
        res = self.queryFan(fanIP, msg)
        if res:
            self.fan_auto[dev.id] = res
            dev.updateStateOnServer('fan_motion', res)

        msg = "<%s;LIGHT;AUTO;GET>" % ( fanName )
        res = self.queryFan(fanIP, msg)
        if res:
            self.light_auto[dev.id] = res
            dev.updateStateOnServer('light_motion', res)
        
        del self.initializing[dev.id] 

        self.updateStatusString(dev)

        msg = "<%s;DEVICE;ID;GET>" % ( fanName )
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))
        
        
    ########################################
    def deviceStopComm(self, dev):
        self.DebugMsg("Stopping device  %s." % dev.name)
        del self.MAC[dev.id]
        del self.light_level[dev.id]
        del self.fan_level[dev.id]

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
                        if dev.pluginProps['fanName'] != fanName and self.MAC[dev.id] != fanName:
                            continue

                        if dev.id in self.initializing and self.initializing[dev.id]:
                            self.DebugMsg(u"initializing.  ignoring %s" % (data))
                            continue

                        if ';LIGHT;LEVEL;ACTUAL;' in cmdStr:
                            if self.light_level[dev.id] != params[4]:
                                dev.updateStateOnServer('brightness', int(params[4]))
                                self.light_level[dev.id] = params[4]
                                self.updateStatusString(dev)
                        elif ';FAN;SPD;CURR;' in cmdStr:
                            if self.fan_level[dev.id] != params[4]:
                                dev.updateStateOnServer('speed', int(params[4]))
                                self.fan_level[dev.id] = params[4]
                                self.updateStatusString(dev)
                        elif ';FAN;AUTO;' in cmdStr:
                            if self.fan_auto[dev.id] != params[3]:
                                dev.updateStateOnServer('fan_motion', params[3])
                                self.fan_auto[dev.id] = params[3]
                        elif ';LIGHT;AUTO;' in cmdStr:
                            if self.light_auto[dev.id] != params[3]:
                                dev.updateStateOnServer('light_motion', params[3])
                                self.light_auto[dev.id] = params[3]
                        elif ';LIGHT;PWR;' in cmdStr:
                            if self.light[dev.id] != params[3]:
                                dev.updateStateOnServer('light', (params[3] == 'ON'))
                                self.light[dev.id] = params[3]
                        elif ';FAN;PWR;' in cmdStr:
                            if self.fan[dev.id] != params[3]:
                                dev.updateStateOnServer('fan', (params[3] == 'ON'))
                                self.fan[dev.id] = params[3]
                        elif ';DEVICE;ID;' in cmdStr:
                            if self.MAC[dev.id] != params[3]:
                                self.MAC[dev.id] = params[3] # MAC address
				props = dev.pluginProps
                                props["address"] = addr[0] 
                                dev.replacePluginPropsOnServer(props)

                self.sleep(0.1)

        except self.StopThread:
            pass    # Optionally catch the StopThread exception and do any needed cleanup.

    ########################################
    def validateDeviceConfigUi(self, valuesDict, typeId, devId):
        fanIP = valuesDict['fanIP']
        fanName = valuesDict['fanName']
        errorsDict = indigo.Dict()
        anError = False

        matchObj = re.match('^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$', fanIP)
        if not matchObj:
            errorsDict['fanIP'] = "Invalid IP address"
            anError = True

        if not fanName or not len(fanName):
            errorsDict['fanName'] = "Fan name can not be blank"
            anError = True


        if anError:
            return (False, valuesDict, errorsDict)
            
        msg = "<%s;DEVICE;ID;GET>" % ( fanName )
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

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

