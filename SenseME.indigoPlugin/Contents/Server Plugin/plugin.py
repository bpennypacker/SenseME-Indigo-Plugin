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
        self.watch = {}
        self.light_level = {}
        self.fan_speed = {}
        self.light_motion = {}
        self.fan_motion = {}

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
    def deviceStartComm(self, dev):
        self.light_level[dev.name] = '-1'
        self.fan_speed[dev.name] = '-1'
        self.light_motion[dev.name] = '?'
        self.fan_motion[dev.name] = '?'
        self.DebugMsg("Starting device  %s." % dev.name)
        
    ########################################
    def deviceStopComm(self, dev):
        del self.light_level[dev.name]
        del self.fan_speed[dev.name]
        del self.light_motion[dev.name]
        del self.fan_motion[dev.name]
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

                        # Update the internal tracking state, and also notify Indigo
                        # of any state changes
                        if ';LIGHT;LEVEL;ACTUAL;' in cmdStr:
                            if dev.name in self.light_level and self.light_level[dev.name] == params[4]:
                                continue
                            self.light_level[dev.name] = params[4]
                            self.DebugMsg(u"set brightness state to %s" % (params[4]))

                        elif ';FAN;SPD;CURR;' in cmdStr:
                            if dev.name in self.fan_speed and self.fan_speed[dev.name] == params[4]:
                                continue
                            self.fan_speed[dev.name] = params[4]
                            self.DebugMsg(u"set speed state to %s" % (params[4]))

                        elif ';FAN;AUTO;' in cmdStr:
                            if dev.name in self.fan_motion and self.fan_motion[dev.name] == params[3]:
                                continue
                            self.fan_motion[dev.name] = params[3] 
                            self.DebugMsg(u"set fan motion state to %s" % (params[3]))

                        elif ';LIGHT;AUTO;' in cmdStr:
                            if dev.name in self.light_motion and self.light_motion[dev.name] == params[3]:
                                continue
                            self.light_motion[dev.name] = params[3] 
                            self.DebugMsg(u"set light motion state to %s" % (params[3]))

                        # If watching for an event due to an action being invoked then swallow
                        # all events for the device until we see the one we're waiting for.
                        if dev.name in self.watch:
                            if self.watch[dev.name] in cmdStr:
                                del self.watch[dev.name]
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
    def doCommand(self, dev, msg, fanIP, watch_for):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.watch[dev.name] = watch_for
        sock.sendto(msg, (fanIP, 31415))
        for i in range(100):
            if dev.name in self.watch:
                self.sleep(0.1)
            else:
                return True
        return False

    ########################################
    def setFanLightOn(self, action):
        dev = indigo.devices[action.deviceId]

        if int(self.light_level[dev.name]) > 0:
            self.DebugMsg(u"%s light level already > 0. Not doing anything." % ( dev.name ))
            return

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;LIGHT;PWR;ON>" % ( fanName )

        if self.doCommand(dev, msg, fanIP, 'LIGHT;LEVEL;ACTUAL'):
            self.DebugMsg(u"Received fan light on response for %s" % (fanName))
        else:
            indigo.server.log("Timeout waiting for response from %s for %s" % ( fanIP, msg ), isError=True)

    ########################################
    def setFanLightOff(self, action):
        dev = indigo.devices[action.deviceId]

        if self.light_level[dev.name] == '0':
            self.DebugMsg(u"%s light level already 0. Not doing anything." % ( dev.name ))
            return

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;LIGHT;PWR;OFF>" % ( fanName )

        if self.doCommand(dev, msg, fanIP, 'LIGHT;LEVEL;ACTUAL'):
            self.DebugMsg(u"Received fan light off response for %s" % (fanName))
        else:
            indigo.server.log("Timeout waiting for response from %s for %s" % ( fanIP, msg ), isError=True)

    ########################################
    def setFanLightBrightness(self, action):
        dev = indigo.devices[action.deviceId]

        lightLevel = action.props.get("lightLevel")

        self.DebugMsg(u"set brightness to %s" % (lightLevel))

        if self.light_level[dev.name] == lightLevel:
            self.DebugMsg(u"%s light level already %s. Not doing anything." % ( dev.name, lightLevel ))
            return

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;LIGHT;LEVEL;SET;%s>" % ( fanName, lightLevel)

        if self.doCommand(dev, msg, fanIP, 'LIGHT;LEVEL;ACTUAL'):
            self.DebugMsg(u"Received fan light on response for %s" % (fanName))
        else:
            indigo.server.log("Timeout waiting for response from %s for %s" % ( fanIP, msg ), isError=True)

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
        self.DebugMsg(u"sending raw command %s to %s" % (cmd, fanIP))
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(cmd, (fanIP, 31415))

    ########################################
    def setFanSpeed(self, action):
        dev = indigo.devices[action.deviceId]

        speed = action.props.get("speed")

        self.DebugMsg(u"set speed to %s" % (speed))

        if self.fan_speed[dev.name] == speed:
            self.DebugMsg(u"%s fan speed already %s. Not doing anything." % ( dev.name, speed ))
            return

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;FAN;SPD;SET;%s>" % ( fanName, speed )
        waitfor = "FAN;SPD;CURR"

        if self.doCommand(dev, msg, fanIP, waitfor):
            self.DebugMsg(u"Received fan speed response for %s" % (fanName))
        else:
            indigo.server.log("Timeout waiting for response %s from %s for %s" % ( waitfor, fanIP, msg ), isError=True)

    ########################################
    def setFanOn(self, action):
        dev = indigo.devices[action.deviceId]

        if int(self.fan_speed[dev.name]) > 0:
            self.DebugMsg(u"%s fan level already > 0. Not doing anything." % ( dev.name ))
            return

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;FAN;PWR;ON>" % ( fanName )
        waitfor = 'FAN;PWR;ON';

        if self.doCommand(dev, msg, fanIP, waitfor):
            self.DebugMsg(u"Received fan on response for %s" % (fanName))
        else:
            indigo.server.log("Timeout waiting for response %s from %s for %s" % ( waitfor, fanIP, msg ), isError=True)

    ########################################
    def setFanOff(self, action):
        dev = indigo.devices[action.deviceId]

        if int(self.fan_speed[dev.name]) == 0:
            self.DebugMsg(u"%s fan level already 0. Not doing anything." % ( dev.name ))
            return

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;FAN;PWR;OFF>" % ( fanName )
        waitfor = 'FAN;PWR;OFF';

        if self.doCommand(dev, msg, fanIP, waitfor):
            self.DebugMsg(u"Received fan off response for %s" % (fanName))
        else:
            indigo.server.log("Timeout waiting for response %s from %s for %s" % ( waitfor, fanIP, msg ), isError=True)

    ########################################
    def setFanMotionSensorOff(self, action):
        dev = indigo.devices[action.deviceId]

        if self.fan_motion[dev.name] == "OFF":
            self.DebugMsg(u"%s fan motion sensor already off. Not doing anything." % ( dev.name ))
            return

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;FAN;AUTO;OFF>" % ( fanName )
        waitfor = 'FAN;AUTO;OFF';

        if self.doCommand(dev, msg, fanIP, waitfor):
            self.DebugMsg(u"Received fan motion sensor off response for %s" % (fanName))
        else:
            indigo.server.log("Timeout waiting for response %s from %s for %s" % ( waitfor, fanIP, msg ), isError=True)

    ########################################
    def setFanMotionSensorOn(self, action):
        dev = indigo.devices[action.deviceId]

        if self.fan_motion[dev.name] == "ON":
            self.DebugMsg(u"%s fan motion sensor already on. Not doing anything." % ( dev.name ))
            return

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;FAN;AUTO;ON>" % ( fanName )
        waitfor = 'FAN;AUTO;ON';

        if self.doCommand(dev, msg, fanIP, waitfor):
            self.DebugMsg(u"Received fan motion sensor off response for %s" % (fanName))
        else:
            indigo.server.log("Timeout waiting for response %s from %s for %s" % ( waitfor, fanIP, msg ), isError=True)

    ########################################
    def setLightMotionSensorOff(self, action):
        dev = indigo.devices[action.deviceId]

        if self.light_motion[dev.name] == "OFF":
            self.DebugMsg(u"%s light motion sensor already off. Not doing anything." % ( dev.name ))
            return

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;LIGHT;AUTO;OFF>" % ( fanName )
        waitfor = 'LIGHT;AUTO;OFF';

        if self.doCommand(dev, msg, fanIP, waitfor):
            self.DebugMsg(u"Received light motion sensor off response for %s" % (fanName))
        else:
            indigo.server.log("Timeout waiting for response %s from %s for %s" % ( waitfor, fanIP, msg ), isError=True)

    ########################################
    def setLightMotionSensorOn(self, action):
        dev = indigo.devices[action.deviceId]

        if self.light_motion[dev.name] == "ON":
            self.DebugMsg(u"%s light motion sensor already on. Not doing anything." % ( dev.name ))
            return

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;LIGHT;AUTO;ON>" % ( fanName )
        waitfor = 'LIGHT;AUTO;ON';

        if self.doCommand(dev, msg, fanIP, waitfor):
            self.DebugMsg(u"Received light motion sensor off response for %s" % (fanName))
        else:
            indigo.server.log("Timeout waiting for response %s from %s for %s" % ( waitfor, fanIP, msg ), isError=True)
