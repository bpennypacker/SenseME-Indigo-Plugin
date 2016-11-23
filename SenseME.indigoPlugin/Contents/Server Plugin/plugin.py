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
    def __init__(self, q, devID, fanIP, timeoutMinutes):
        threading.Thread.__init__(self)
        self.q = q
        self.fanIP = fanIP
        self.devID = devID
        self.sock = None
        self.timeoutMinutes = timeoutMinutes
        self.tick = 0
        self.stoprequest = threading.Event()

    def __connect_to_fan(self, reinit):
        if reinit == True:
            time.sleep(3)

        if self.sock == None:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

            # Enable keepalive
            TCP_KEEPALIVE = 0x10
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            self.sock.setsockopt(socket.IPPROTO_TCP, TCP_KEEPALIVE, 60)

        try:
            self.q.put((MSG_DEBUG, self.devID, "Thread connecting to " + self.fanIP))
            self.sock.connect((self.fanIP, 31415))
            self.tick = int(time.time())

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

        while not self.stoprequest.isSet():
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

            if self.timeoutMinutes > 0 and int(time.time()) - self.tick > (self.timeoutMinutes * 60 ): 
                self.q.put((MSG_DEBUG, self.devID, "No messages from fan in %d minutes. Reinitializing connection." % ( self.timeoutMinutes)))
                self.sock.close()
                self.sock = None

        if self.sock != None:
            self.sock.close()

        self.q.put((MSG_DEBUG, self.devID, "Terminating thread"))

    def join(self, timeout=None):
        self.stoprequest.set()
        super(FanListener, self).join(timeout)


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
    def queryFan(self, fanIP, msg, regexp = '\(.*;([^;]+)\)', receive = True):
        status = ''
        try:
            sock = socket.socket()
            sock.settimeout(5)
            sock.connect((fanIP, 31415))

            sock.send(msg)

            if receive == False:
                self.DebugMsg("sent %s" % (msg))
                sock.close()
                return True

            status = sock.recv(1024)
            sock.close()
        except socket.error as e:
            self.DebugMsg("queryFan %s %s failed. %s" % (fanIP, msg, str(e)))
            return False

        matchObj = re.match(regexp, status)
        if matchObj:
            self.DebugMsg("query %s returned: %s" % ( msg, matchObj.group(1) ))
            return (matchObj.group(1))
        else:
            self.DebugMsg("query %s returned: unknown: %s" % ( msg, status ))
            return False
    
    
    ########################################
    def updateStatusString(self, fan):
        dev = fan['dev']
        if fan['light'] == 'OFF':
            l = 'off'
        else:
            l = 'on'

        if fan['fan'] == 'OFF':
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
        elif fan['fan_level'] in [ '5', '6', '7' ]:
            dev.updateStateImageOnServer(indigo.kStateImageSel.FanHigh) 
        else:
            dev.updateStateImageOnServer(indigo.kStateImageSel.Error) 


    ########################################
    def getFanStatus(self, fan):
        dev = fan['dev']
        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']

        msg = "<%s;DEVICE;ID;GET>" % ( fanName )
        res = self.queryFan(fanIP, msg, regexp = '\(.*;([^;]+);[^;]+\)')
        if res:
            fan['MAC'] = res
            fanName = res
            props = dev.pluginProps
            props["fanMAC"] = res
            dev.replacePluginPropsOnServer(props)
            
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

        msg = "<%s;SMARTMODE;ACTUAL;GET>" % ( fanName )
        res = self.queryFan(fanIP, msg)
        if res:
            fan['smartmode'] = res
            dev.updateStateOnServer('smartmode', res)

        msg = "<%s;SNSROCC;STATUS;GET>" % ( fanName )
        res = self.queryFan(fanIP, msg)
        if res:
            fan['motion'] = res
            dev.updateStateOnServer('motion', (res == 'OCCUPIED'))

        msg = "<%s;DEVICE;SERVER;SET;PRODUCTION>" % ( fanName )
        res = self.queryFan(fanIP, msg, receive = False)

        self.updateStatusString(fan)

    ########################################
    def deviceStartComm(self, dev):
        global fan_queue

        dev.stateListOrDisplayStateIdChanged() # in case any states added/removed after plugin upgrade

        timeout = 0

        try:
            if 'timeoutValue' in self.pluginPrefs and int(self.pluginPrefs['timeoutValue']) > 0:
                timeout = int(self.pluginPrefs['timeoutValue'])
        except:
            self.DebugMsg("invalid value in timeout setting: %s" % ( self.pluginPrefs['timeoutValue'] ))

        self.DebugMsg("Starting device '%s'" % dev.name)

        if dev.id in self.allfans:
            self.DebugMsg("Found device %d already running. Ignoring..." % ( dev.id ))
            return

        fanIP = dev.pluginProps['fanIP']

        thread = FanListener(fan_queue, dev.id, fanIP, timeout)

        fan = {
                'thread'          : thread,
                'MAC'             : '',
                'light'           : '',
                'fan'             : '',
                'light_level'     : '',
                'fan_level'       : '',
                'light_auto'      : '',
                'fan_auto'        : '',
                'smartmode'       : '',
                'motion'          : '',
                'dev'             : dev
              }

        self.allfans[dev.id] = fan

        self.getFanStatus(fan)

        thread.daemon = True
        thread.start()

    ########################################
    def deviceStopComm(self, dev):
        self.DebugMsg("Stopping device %s." % dev.name)

        fan = self.allfans[dev.id]

        if fan: 
            fan['thread'].join()
            del self.allfans[dev.id]

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
                        self.DebugMsg('Changing light level to %s' % (params[4]))
                        dev.updateStateOnServer('brightness', int(params[4]))
                    fan['light_level'] = params[4]
                    self.updateStatusString(fan)
            elif ';FAN;SPD;ACTUAL;' in cmdStr:
                if fan['fan_level'] != params[4]:
                    if fan['fan_level'] != '':
                        self.DebugMsg('Changing fan speed to %s' % (params[4]))
                        dev.updateStateOnServer('speed', int(params[4]))
                    fan['fan_level'] = params[4]
                    self.updateStatusString(fan)
            elif ';FAN;AUTO;' in cmdStr:
                if fan['fan_auto'] != params[3]:
                    if fan['fan_auto'] != '':
                        self.DebugMsg('Changing fan motion to %s' % (params[3]))
                        dev.updateStateOnServer('fan_motion', params[3])
                    fan['fan_auto'] = params[3]
            elif ';LIGHT;AUTO;' in cmdStr:
                if fan['light_auto'] != params[3]:
                    if fan['light_auto'] != '':
                        self.DebugMsg('Changing light motion to %s' % (params[3]))
                        dev.updateStateOnServer('light_motion', params[3])
                    fan['light_auto'] = params[3]
            elif ';LIGHT;PWR;' in cmdStr:
                if fan['light'] != params[3]:
                    if fan['light'] != '':
                        self.DebugMsg('Changing light to %s' % (params[3]))
                        dev.updateStateOnServer('light', (params[3] == 'ON'))
                    fan['light'] = params[3]
                    self.updateStatusString(fan)
            elif ';FAN;PWR;' in cmdStr:
                if fan['fan'] != params[3]:
                    if fan['fan'] != '':
                        self.DebugMsg('Changing fan to %s' % (params[3]))
                        dev.updateStateOnServer('fan', (params[3] == 'ON'))
                    fan['fan'] = params[3]
                    self.updateStatusString(fan)
            elif ';DEVICE;ID;' in cmdStr:
                if fan['MAC'] != params[3]:
                    props = dev.pluginProps
                    props["address"] = addr[0] 
                    self.DebugMsg("Setting MAC to '%s'" % ( params[3] ))
                    fan['MAC'] = params[3] # MAC address
                    props["fanMAC"] = fan['MAC']
                    dev.replacePluginPropsOnServer(props)
            elif ';SMARTMODE;ACTUAL' in cmdStr:
                if fan['smartmode'] != params[3]:
                    if fan['smartmode'] != '':
                        self.DebugMsg('Changing smartmode to %s' % (params[3]))
                        dev.updateStateOnServer('smartmode', params[3])
                    fan['smartmode'] = params[3]
            elif ';SNSROCC;STATUS;' in cmdStr:
                if fan['motion'] != params[3]:
                    if fan['motion'] != '':
                        self.DebugMsg('Changing motion to %s' % (params[3]))
                        dev.updateStateOnServer('motion', (params[3] == 'OCCUPIED'))
                    fan['motion'] = params[3]

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
                if i < 0 or i > 7:
                    errorDict = indigo.Dict()
                    errorDict["speed"] = "Speed must be an integer between 0 and 7"
                    return (False, valuesDict, errorDict)
                else:
                    return True
            except:
                errorDict = indigo.Dict()
                errorDict["speed"] = "Speed must be an integer between 0 and 7"
                return (False, valuesDict, errorDict)
        elif typeId == 'fanLearnMinSpeed':
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
        elif typeId == 'fanLearnMaxSpeed':
            try:
                i = int(valuesDict['speed'])
                if i < 1 or i > 7:
                    errorDict = indigo.Dict()
                    errorDict["speed"] = "Speed must be an integer between 1 and 7"
                    return (False, valuesDict, errorDict)
                else:
                    return True
            except:
                errorDict = indigo.Dict()
                errorDict["speed"] = "Speed must be an integer between 1 and 7"
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
        fan = self.allfans[dev.id]

        f = fan['MAC']
        if f == '':
            f = dev.pluginProps['fanName']

        speed = action.props.get("speed")

        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;FAN;SPD;SET;%s>" % ( f, speed )

        self.DebugMsg("Sending %s" % ( msg ))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setFanOn(self, action):
        dev = indigo.devices[action.deviceId]
        fan = self.allfans[dev.id]

        f = fan['MAC']
        if f == '':
            f = dev.pluginProps['fanName']

        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;FAN;PWR;ON>" % ( f )

        self.DebugMsg("Sending %s" % ( msg ))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setFanOff(self, action):
        dev = indigo.devices[action.deviceId]
        fan = self.allfans[dev.id]

        f = fan['MAC']
        if f == '':
            f = dev.pluginProps['fanName']

        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;FAN;PWR;OFF>" % ( f )

        self.DebugMsg("Sending %s" % ( msg ))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setFanMotionSensorOff(self, action):
        dev = indigo.devices[action.deviceId]
        fan = self.allfans[dev.id]

        f = fan['MAC']
        if f == '':
            f = dev.pluginProps['fanName']

        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;FAN;AUTO;OFF>" % ( f )

        self.DebugMsg("Sending %s" % ( msg ))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setFanMotionSensorOn(self, action):
        dev = indigo.devices[action.deviceId]
        fan = self.allfans[dev.id]

        f = fan['MAC']
        if f == '':
            f = dev.pluginProps['fanName']

        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;FAN;AUTO;ON>" % ( f )

        self.DebugMsg("Sending %s" % ( msg ))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setLightMotionSensorOff(self, action):
        dev = indigo.devices[action.deviceId]
        fan = self.allfans[dev.id]

        f = fan['MAC']
        if f == '':
            f = dev.pluginProps['fanName']

        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;LIGHT;AUTO;OFF>" % ( f )

        self.DebugMsg("Sending %s" % ( msg ))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setLightMotionSensorOn(self, action):
        dev = indigo.devices[action.deviceId]
        fan = self.allfans[dev.id]

        f = fan['MAC']
        if f == '':
            f = dev.pluginProps['fanName']

        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;LIGHT;AUTO;ON>" % ( f )

        self.DebugMsg("Sending %s" % ( msg ))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def enableFanSmartHeating(self, action):
        dev = indigo.devices[action.deviceId]
        fan = self.allfans[dev.id]

        f = fan['MAC']
        if f == '':
            f = dev.pluginProps['fanName']

        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;SMARTMODE;STATE;SET;HEATING>" % ( f )

        self.DebugMsg("Sending %s" % ( msg ))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def enableFanSmartCooling(self, action):
        dev = indigo.devices[action.deviceId]
        fan = self.allfans[dev.id]

        f = fan['MAC']
        if f == '':
            f = dev.pluginProps['fanName']

        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;SMARTMODE;STATE;SET;COOLING>" % ( f )

        self.DebugMsg("Sending %s" % ( msg ))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def disableFanSmartMode(self, action):
        dev = indigo.devices[action.deviceId]
        fan = self.allfans[dev.id]

        f = fan['MAC']
        if f == '':
            f = dev.pluginProps['fanName']

        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;SMARTMODE;STATE;SET;OFF>" % ( f )

        self.DebugMsg("Sending %s" % ( msg ))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setFanSmartModeMinSpeed(self, action):
        dev = indigo.devices[action.deviceId]
        fan = self.allfans[dev.id]

        f = fan['MAC']
        if f == '':
            f = dev.pluginProps['fanName']

        speed = action.props.get("speed")

        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;LEARN;MINSPEED;SET;%d>" % ( f, int(speed) )

        self.DebugMsg("Sending %s" % ( msg ))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setFanSmartModeMaxSpeed(self, action):
        dev = indigo.devices[action.deviceId]
        fan = self.allfans[dev.id]

        f = fan['MAC']
        if f == '':
            f = dev.pluginProps['fanName']

        speed = action.props.get("speed")

        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;LEARN;MAXSPEED;SET;%s>" % ( f, speed )

        self.DebugMsg("Sending %s" % ( msg ))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

