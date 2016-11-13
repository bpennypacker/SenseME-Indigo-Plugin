#! /usr/bin/env python
# -*- coding: utf-8 -*-
# http://forums.indigodomo.com/viewtopic.php?f=108&t=16001&start=0

import indigo
import indigoPluginUpdateChecker

import os
import sys
import socket
import select
import re
import time
import threading
import Queue

fan_queue = Queue.Queue(maxsize=1000)

MSG_FAN = 1
MSG_DEBUG = 2
MSG_REINIT = 3


################################################################################
# Create a thread that just establishes a TCP connection with a fan. Any
# data received by the fan is added to the main threads queue so that the
# main thread can process it.
class FanListener(threading.Thread):
    def __init__(self, q, devID, fanIP):
        threading.Thread.__init__(self)
        self.q = q
        self.fanIP = fanIP
        self.devID = devID
        self.sock = None
        self.tick = 0

    def __connect_to_fan(self, reinit):
        if self.sock == None:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        try:
            self.q.put((MSG_DEBUG, self.devID, "Thread connecting to " + self.fanIP))
            self.sock.connect((self.fanIP, 31415))
            self.tick = int(time.time())

            # enable keepalive
#            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

        except socket.error as e:
            msg = "Bind to %s failed. Error %s : %s" % (self.fanIP, str(e[0]), e[1])
            self.q.put((MSG_DEBUG, self.devID, msg))
            self.sock.close()
            self.sock = None
            return

        if reinit == True:
            self.q.put((MSG_REINIT, self.devID, ""))

        self.sock.setblocking(0)

    def run(self):
        self.__connect_to_fan(False)

        while True:
            try: 
                while self.sock == None: 
                    self.__connect_to_fan(True)
                    if self.sock == None:
                        self.q.put((MSG_DEBUG, self.devID, "Connection failed.  Sleeping for 60 seconds."))
                        time.sleep(60)

                ready = select.select([self.sock], [], [], 5)
                if ready[0]:
                    data = self.sock.recv(1024)
                    self.tick = int(time.time())
                    self.q.put((MSG_FAN, self.devID, data))

            except socket.error as e: 
                msg = "%s socket error %s : %s" % (self.fanIP, str(e[0]), e[1])
                self.q.put((MSG_DEBUG, self.devID, msg))
                self.sock.close()
                self.sock = None
            except Exception as e:
                msg = "%s generic error %s : %s" % (self.fanIP, str(e[0]), e[1])
                self.q.put((MSG_DEBUG, self.devID, msg))

            if int(time.time()) - self.tick > 1800: 
                self.q.put((MSG_DEBUG, self.devID, "No messages from fan in 30 minutes. Reinitializing connection." ))
                self.sock.close()
                self.sock = None


################################################################################
class Plugin(indigo.PluginBase):
    ########################################
    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        indigo.PluginBase.__init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs)
        self.debug = True

        self.allfans = {}

        self.updater = indigoPluginUpdateChecker.updateChecker(self, 'http://bruce.pennypacker.org/files/PluginVersions/SenseME.html', 7)

    ########################################
    def __del__(self):
        indigo.PluginBase.__del__(self)

    ########################################
    def DebugMsg(self, msg):
        if 'debug' in self.pluginPrefs and self.pluginPrefs['debug'] == True:
            self.debugLog(msg)

    ########################################
    def startup(self):
        self.DebugMsg(u'startup called')

    ########################################
    def shutdown(self):
        self.DebugMsg(u"shutdown called")

    ########################################
    def queryFan(self, fanIP, msg):
        status = ''
        try:
            sock = socket.socket()
            sock.settimeout(5)
            sock.connect((fanIP, 31415))

            sock.send(msg)
            status = sock.recv(1024)
            sock.close()
        except socket.error as e:
            self.DebugMsg("queryFan %s failed. %s" % (fanIP, str(e)))
            return False

        matchObj = re.match('\(.*;([^;]+)\)', status)
        if matchObj:
            self.DebugMsg("query %s returned: %s" % ( msg, matchObj.group(1) ))
            return (matchObj.group(1))
        else:
            self.DebugMsg("query %s returned: unknown: %s" % ( msg, status ))
            return False
    
    
    ########################################
    def updateStatusString(self, fan):
        dev = fan['dev']
        if fan['light_level'] == '0':
            l = 'off'
        else:
            l = 'on'

        if fan['fan_level'] == '0':
            f = 'off'
        else:
            f = 'on'

        if 'debug' in self.pluginPrefs and self.pluginPrefs['debug']:
            s = "%s / %s (f:%s, l:%s)" % (f, l, fan['fan_level'], fan['light_level'])
        else:
          s = "%s / %s" % (f, l)

        dev.updateStateOnServer('statusString', s)

        if fan['fan_level'] == '0':
            dev.updateStateImageOnServer(indigo.kStateImageSel.FanOff) 
        elif fan['fan_level'] in [ '1', '2' ]:
            dev.updateStateImageOnServer(indigo.kStateImageSel.FanLow) 
        elif fan['fan_level'] in [ '3', '4' ]:
            dev.updateStateImageOnServer(indigo.kStateImageSel.FanMedium) 
        elif fan['fan_level'] in [ '5', '6' ]:
            dev.updateStateImageOnServer(indigo.kStateImageSel.FanHigh) 
        else:
            dev.updateStateImageOnServer(indigo.kStateImageSel.Error) 


    ########################################
    def getFanStatus(self, fan):
        dev = fan['dev']
        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']

        msg = "<%s;LIGHT;PWR;GET>" % ( fanName )
        res = self.queryFan(fanIP, msg)
        if res:
            fan['light'] = res
            dev.updateStateOnServer('light', (res == 'ON'))

        msg = "<%s;FAN;PWR;GET>" % ( fanName )
        res = self.queryFan(fanIP, msg)
        if res:
            fan['fan'] = res
            dev.updateStateOnServer('fan', (res == 'ON'))

        msg = "<%s;FAN;SPD;GET;ACTUAL>" % ( fanName )
        res = self.queryFan(fanIP, msg)
        if res:
            fan['fan_level'] = res
            dev.updateStateOnServer('speed', int(res))

        msg = "<%s;LIGHT;LEVEL;GET;ACTUAL>" % ( fanName )
        res = self.queryFan(fanIP, msg)
        if res:
            if (res == 'NOT PRESENT'):
                res = '0'
            dev.updateStateOnServer('brightness', int(res))
            fan['light_level'] = res

        msg = "<%s;FAN;AUTO;GET>" % ( fanName )
        res = self.queryFan(fanIP, msg)
        if res:
            fan['fan_auto'] = res
            dev.updateStateOnServer('fan_motion', res)

        msg = "<%s;LIGHT;AUTO;GET>" % ( fanName )
        res = self.queryFan(fanIP, msg)
        if res:
            fan['light_auto'] = res
            dev.updateStateOnServer('light_motion', res)
            
        self.updateStatusString(fan)
                

    ########################################
    def deviceStartComm(self, dev):
        global fan_queue

        self.DebugMsg("Starting device  %s." % dev.name)

        fanIP = dev.pluginProps['fanIP']

        thread = FanListener(fan_queue, dev.id, fanIP)

        fan = {
                'thread'          : thread,
                'MAC'             : '',
                'light'           : '',
                'fan'             : '',
                'light_level'     : '',
                'fan_level'       : '',
                'light_auto'      : '',
                'fan_auto'        : '',
                'dev'             : dev
              }

        self.allfans[dev.id] = fan

        self.getFanStatus(fan)

        thread.daemon = True
        thread.start()

    ########################################
    def deviceStopComm(self, dev):
        self.DebugMsg("Stopping device  %s." % dev.name)


    ########################################
    def processFanMessage(self, fan, data):

        self.DebugMsg('processing message %s' % (data))

        matchObj = re.match('\((.*)\)', data)
        if matchObj:
            cmdStr = matchObj.group(1)
            params = cmdStr.split(';')
            fanName = params[0]

            dev = fan['dev']

            if ';LIGHT;LEVEL;ACTUAL;' in cmdStr:
                if fan['light_level'] != params[4]:
                    if fan['light_level'] != '':
                        dev.updateStateOnServer('brightness', int(params[4]))
                    fan['light_level'] = params[4]
                    self.updateStatusString(fan)
            elif ';FAN;SPD;CURR;' in cmdStr:
                if fan['fan_level'] != params[4]:
                    if fan['fan_level'] != '':
                        dev.updateStateOnServer('speed', int(params[4]))
                    fan['fan_level'] = params[4]
                    self.updateStatusString(dev)
            elif ';FAN;AUTO;' in cmdStr:
                if fan['fan_auto'] != params[3]:
                    if fan['fan_auto'] != '':
                        dev.updateStateOnServer('fan_motion', params[3])
                    fan['fan_auto'] = params[3]
            elif ';LIGHT;AUTO;' in cmdStr:
                if fan['light_auto'] != params[3]:
                    if fan['light_auto'] != '':
                        dev.updateStateOnServer('light_motion', params[3])
                    fan['light_auto'] = params[3]
            elif ';LIGHT;PWR;' in cmdStr:
                if fan['light'] != params[3]:
                    if fan['light'] != '':
                        dev.updateStateOnServer('light', (params[3] == 'ON'))
                    fan['light'] = params[3]
            elif ';FAN;PWR;' in cmdStr:
                if fan['fan'] != params[3]:
                    if fan['fan'] != '':
                        dev.updateStateOnServer('fan', (params[3] == 'ON'))
                    fan['fan'] = params[3]
            elif ';DEVICE;ID;' in cmdStr:
                if fan['MAC'] != params[3]:
                    props = dev.pluginProps
                    props["address"] = addr[0] 
                    dev.replacePluginPropsOnServer(props)
                    fan['MAC'] = params[3] # MAC address

    ########################################
    def runConcurrentThread(self):
        global fan_queue

        self.DebugMsg(u"starting runConcurrentThread()")

        while True:
            try:
                msg = fan_queue.get(block=False)

                msgtype = msg[0]
                devID = msg[1]
                data = msg[2]

                fan = self.allfans[devID]
                name = fan['dev'].pluginProps['fanName']

                if msgtype == MSG_DEBUG:
                    self.DebugMsg('%s : %s' % (name, data))

                elif msgtype == MSG_REINIT:
                    fan['light'] = ''
                    fan['fan'] = ''
                    fan['light_level'] = ''
                    fan['fan_level'] = ''
                    fan['light_auto'] = ''
                    fan['fan_auto'] = ''
                    self.getFanStatus(fan)

                elif msgtype == MSG_FAN:
                    self.processFanMessage(fan, data)

                fan_queue.task_done()

            except Queue.Empty as e:
                self.sleep(1)
                continue
        
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
        msg = "<%s;LIGHT;LEVEL;SET;%s>" % (fanName, lightLevel)

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
            self.DebugMsg(u"sending command %s to %s" % (cmd, fanIP))
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(cmd, (fanIP, 31415))

    ########################################
    def setFanSpeed(self, action):
        dev = indigo.devices[action.deviceId]

        speed = action.props.get("speed")


        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;FAN;SPD;SET;%s>" % ( fanName, speed )

        self.DebugMsg("Sending %s" % ( msg ))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setFanOn(self, action):
        dev = indigo.devices[action.deviceId]

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;FAN;PWR;ON>" % ( fanName )

        self.DebugMsg("Sending %s" % ( msg ))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setFanOff(self, action):
        dev = indigo.devices[action.deviceId]

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;FAN;PWR;OFF>" % ( fanName )

        self.DebugMsg("Sending %s" % ( msg ))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setFanMotionSensorOff(self, action):
        dev = indigo.devices[action.deviceId]

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;FAN;AUTO;OFF>" % ( fanName )

        self.DebugMsg("Sending %s" % ( msg ))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setFanMotionSensorOn(self, action):
        dev = indigo.devices[action.deviceId]

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;FAN;AUTO;ON>" % ( fanName )

        self.DebugMsg("Sending %s" % ( msg ))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setLightMotionSensorOff(self, action):
        dev = indigo.devices[action.deviceId]

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;LIGHT;AUTO;OFF>" % ( fanName )

        self.DebugMsg("Sending %s" % ( msg ))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setLightMotionSensorOn(self, action):
        dev = indigo.devices[action.deviceId]

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;LIGHT;AUTO;ON>" % ( fanName )

        self.DebugMsg("Sending %s" % ( msg ))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))
