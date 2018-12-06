#! /usr/bin/env python
# -*- coding: utf-8 -*-

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
    def __init__(self, q, devID, fanIP, timeoutMinutes, fanID):
        threading.Thread.__init__(self)
        self.q = q
        self.fanIP = fanIP
        self.devID = devID
        self.sock = None
        self.timeoutMinutes = timeoutMinutes
        self.tick = 0
        self.stoprequest = threading.Event()
        self.fanID = fanID
        self.leftover = ''

    def __connect_to_fan(self, reinit):
        if reinit == True:
            time.sleep(3)

        if self.sock == None:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

            # Enable keepalive
            TCP_KEEPALIVE = 0x10
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            self.sock.setsockopt(socket.IPPROTO_TCP, TCP_KEEPALIVE, 60)

        self.q.put((MSG_DEBUG, self.devID, "Thread connecting to " + self.fanIP))

        try:
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

        # Send a GETALL to the newly connected fan to learn al about it
        msg = "<%s;GETALL>" % ( self.fanID )
        self.sock.send(msg)

        # GETALL apparently doesn't return the status of the motion detector, so also request
        # the motion detector status
        msg = "<%s;SNSROCC;STATUS;GET>" % ( self.fanID )
        self.sock.send(msg)

    def run(self):
        self.__connect_to_fan(False)

        while not self.stoprequest.isSet():
            try:
                while self.sock == None:
                    self.__connect_to_fan(True)
                    if self.sock == None:
                        self.q.put((MSG_DEBUG, self.devID, "Connection failed. Sleeping for 60 seconds."))
                        time.sleep(60)

                ready = select.select([self.sock], [], [], 5)
                if ready[0]:
                    data = self.sock.recv(2048)
                    self.tick = int(time.time())

                    data = self.leftover + data
                    self.leftover = ''

                    # The data received may have multiple parenthesized data points. Split them
                    # and put each individual message onto the queue
                    # Convert "(msg1)(msg2)(msg3)" to "(msg1)|(msg2)|(msg3)" then split on the '|'
                    for p in data.replace(')(', ')|(').split('|'):
                        if p[-1] != ')':
                            self.leftover = p
                            msg = "saving for next select: '%s'" % ( p )
                            self.q.put((MSG_DEBUG, self.devID, msg))
                        else:
                            self.q.put((MSG_FAN, self.devID, p))

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
        if fan['light'] == '' or fan['fan'] == '' or fan['fan_level'] == '' or fan['light_level'] == '' :
            return

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

        if s != fan['status_string']:
            fan['status_string'] = s
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

        fan = {
                'MAC'             : '',
                'light'           : '',
                'fan'             : '',
                'light_level'     : '',
                'fan_level'       : '',
                'light_auto'      : '',
                'fan_auto'        : '',
                'smartmode'       : '',
                'motion'          : '',
                'whoosh'          : '',
                'beep'            : '',
                'indicators'      : '',
                'direction'       : '',
                'coolingIdealTemp': '',
                'sleepIdealTemp'  : '',
                'status_string'   : '',
                'sleepMode'       : '',
                'dev'             : dev
              }

        self.allfans[dev.id] = fan

        self.getFanStatus(fan)

        f = fan['MAC']
        if f == '':
            f = dev.name

        thread = FanListener(fan_queue, dev.id, fanIP, timeout, f)
        fan['thread'] = thread
        thread.daemon = True
        thread.start()

    ########################################
    def debugState(self, action):
        dev = indigo.devices[action.deviceId]
        fan = self.allfans[dev.id]
        self.DebugMsg("dumping fan dict:")
        self.DebugMsg("Fan name    : %s" % ( fan['dev'].name ))
        self.DebugMsg("MAC         : %s" % ( fan['MAC'] ))
        self.DebugMsg("light       : %s" % ( fan['light'] ))
        self.DebugMsg("fan         : %s" % ( fan['fan'] ))
        self.DebugMsg("light level : %s" % ( fan['light_level'] ))
        self.DebugMsg("fan level   : %s" % ( fan['fan_level'] ))
        self.DebugMsg("light auto  : %s" % ( fan['light_auto'] ))
        self.DebugMsg("fan auto    : %s" % ( fan['fan_auto'] ))
        self.DebugMsg("smart mode  : %s" % ( fan['smartmode'] ))
        self.DebugMsg("motion      : %s" % ( fan['motion'] ))
        self.DebugMsg("whoosh      : %s" % ( fan['whoosh'] ))
        self.DebugMsg("beep        : %s" % ( fan['beep'] ))
        self.DebugMsg("indicators  : %s" % ( fan['indicators'] ))
        self.DebugMsg("direction   : %s" % ( fan['direction'] ))
        self.DebugMsg("cooling temp: %s" % ( fan['coolingIdealTemp'] ))
        self.DebugMsg("sleep temp  : %s" % ( fan['sleepIdealTemp'] ))
        self.DebugMsg("status      : %s" % ( fan['status_string'] ))
        self.DebugMsg("sleep mode  : %s" % ( fan['sleepMode'] ))
        self.DebugMsg("dump complete")


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
                    trigger = ( fan['light_level'] != '' )
                    self.DebugMsg('Changing light level to %s (trigger:%s)' % (params[4], trigger))
                    dev.updateStateOnServer('brightness', int(params[4]), triggerEvents = trigger)
                    fan['light_level'] = params[4]
            elif ';FAN;SPD;ACTUAL;' in cmdStr:
                if fan['fan_level'] != params[4]:
                    trigger = ( fan['fan_level'] != '' )
                    self.DebugMsg('Changing fan speed to %s (trigger: %s)' % (params[4], trigger))
                    dev.updateStateOnServer('speed', int(params[4]), triggerEvents = trigger)
                    fan['fan_level'] = params[4]
            elif ';FAN;AUTO;' in cmdStr:
                if fan['fan_auto'] != params[3]:
                    trigger = ( fan['fan_auto'] != '' )
                    self.DebugMsg('Changing fan motion to %s (trigger: %s)' % (params[3], trigger))
                    dev.updateStateOnServer('fan_motion', params[3], triggerEvents = trigger)
                    fan['fan_auto'] = params[3]
            elif ';LIGHT;AUTO;' in cmdStr:
                if fan['light_auto'] != params[3]:
                    trigger = ( fan['light_auto'] != '' )
                    self.DebugMsg('Changing light motion to %s (trigger: %s)' % (params[3], trigger))
                    dev.updateStateOnServer('light_motion', params[3], triggerEvents = trigger)
                    fan['light_auto'] = params[3]
            elif ';LIGHT;PWR;' in cmdStr:
                if fan['light'] != params[3]:
                    trigger = ( fan['light'] != '' )
                    self.DebugMsg('Changing light to %s (trigger: %s)' % (params[3], trigger))
                    dev.updateStateOnServer('light', (params[3] == 'ON'))
                    fan['light'] = params[3]
            elif ';FAN;PWR;' in cmdStr:
                if fan['fan'] != params[3]:
                    trigger = ( fan['fan'] != '' )
                    self.DebugMsg('Changing fan to %s (trigger: %s)' % (params[3], trigger))
                    dev.updateStateOnServer('fan', (params[3] == 'ON'), triggerEvents = trigger)
                    fan['fan'] = params[3]
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
                    trigger = ( fan['smartmode'] != '' )
                    self.DebugMsg('Changing smartmode to %s (trigger: %s)' % (params[3], trigger))
                    dev.updateStateOnServer('smartmode', params[3], triggerEvents = trigger)
                    fan['smartmode'] = params[3]
            elif ';SNSROCC;STATUS;' in cmdStr:
                if fan['motion'] != params[3]:
                    trigger = ( fan['motion'] != '' )
                    self.DebugMsg('Changing motion to %s (trigger: %s)' % (params[3], trigger))
                    dev.updateStateOnServer('motion', (params[3] == 'OCCUPIED'))
                    fan['motion'] = params[3]
            elif ';FAN;WHOOSH;STATUS;' in cmdStr:
                if fan['whoosh'] != params[4]:
                    trigger = ( fan['whoosh'] != '' )
                    self.DebugMsg('Changing whoosh to %s (trigger: %s)' % (params[4], trigger))
                    dev.updateStateOnServer('whoosh', params[4], triggerEvents = trigger)
                    fan['whoosh'] = params[4]
            elif ';DEVICE;BEEPER;' in cmdStr:
                if fan['beep'] != params[3]:
                    trigger = ( fan['beep'] != '' )
                    self.DebugMsg('Changing beep to %s (trigger: %s)' % (params[3], trigger))
                    dev.updateStateOnServer('beep', params[3], triggerEvents = trigger)
                    fan['beep'] = params[3]
            elif ';DEVICE;INDICATORS;' in cmdStr:
                if fan['indicators'] != params[3]:
                    trigger = ( fan['indicators'] != '' )
                    self.DebugMsg('Changing indicators to %s (trigger: %s)' % (params[3], trigger))
                    dev.updateStateOnServer('indicators', params[3], triggerEvents = trigger)
                    fan['indicators'] = params[3]
            elif ';FAN;DIR;' in cmdStr: # FWD or REV
                if params[3] == 'FWD':
                    direction = "forward"
                elif params[3] == 'REV':
                    direction = "reverse"
                else:
                    direction = "unknown"
                if fan['direction'] != direction:
                    trigger = ( fan['direction'] != '' )
                    self.DebugMsg('Changing direction to %s (trigger: %s)' % (direction, trigger))
                    dev.updateStateOnServer('direction', direction, triggerEvents = trigger)
                    fan['direction'] = direction
            elif ';LEARN;ZEROTEMP;' in cmdStr:
                if fan['coolingIdealTemp'] != params[3]:
                    trigger = ( fan['coolingIdealTemp'] != '' )
                    tempUnits = dev.pluginProps['fanTempUnits']
                    if tempUnits == 'C':
                        temp = str(int(params[3]) / 100)
                    else:
                        # Divide by 100, then F = C * 9/5 + 32
                        temp = str(float((int(params[3]) / 100) * 9 ) / 5 + 32)

                    uiTemp = "%s %s" % ( temp, tempUnits )

                    self.DebugMsg('Changing cooling ideal temperature %s %s (trigger: %s)' % (temp, tempUnits, trigger))
                    dev.updateStateOnServer('coolingIdealTemp', temp, triggerEvents = trigger, uiValue = uiTemp)
                    fan['coolingIdealTemp'] = params[3]
            elif ';SMARTSLEEP;IDEALTEMP;' in cmdStr:
                if fan['sleepIdealTemp'] != params[3]:
                    trigger = ( fan['sleepIdealTemp'] != '' )
                    tempUnits = dev.pluginProps['fanTempUnits']
                    if tempUnits == 'C':
                        temp = str(int(params[3]) / 100)
                    else:
                        # Divide by 100, then F = C * 9/5 + 32
                        temp = str(float((int(params[3]) / 100) * 9 ) / 5 + 32)

                    uiTemp = "%s %s" % ( temp, tempUnits )

                    self.DebugMsg('Changing sleep ideal temperature %s %s (trigger: %s)' % (temp, tempUnits, trigger))
                    dev.updateStateOnServer('sleepIdealTemp', temp, triggerEvents = trigger, uiValue = uiTemp)
                    fan['sleepIdealTemp'] = params[3]
            elif ';SLEEP;STATE;' in cmdStr:
                if fan['sleepMode'] != params[3]:
                    trigger = ( fan['sleepMode'] != '' )
                    self.DebugMsg('Changing sleep mode to %s (trigger: %s)' % (params[3], trigger))
                    dev.updateStateOnServer('sleepMode', params[3], triggerEvents = trigger)
                    fan['sleepMode'] = params[3]

            self.updateStatusString(fan)

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
                fan = None

                if devID in self.allfans:
                    fan = self.allfans[devID]
                else:
                    continue

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
                    fan['smartmode'] = ''
                    fan['motion'] = ''
                    fan['whoosh'] = ''
                    fan['beep'] = ''
                    fan['indicators'] = ''
                    fan['direction'] = ''
                    fan['coolingIdealTemp'] = ''
                    fan['sleepIdealTemp'] = ''
                    fan['status_string'] = ''
                    fan['sleepMode'] = ''
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
        tempUnits = valuesDict['fanTempUnits']
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

        dev = indigo.devices[devId]
        fan = self.allfans[dev.id]

        if tempUnits == 'C':
            temp = int(float(fan['coolingIdealTemp']) / 100.0)
            dev.updateStateOnServer('coolingIdealTemp', temp)

            temp = int(float(fan['sleepIdealTemp']) / 100.0)
            dev.updateStateOnServer('sleepIdealTemp', temp)
        else:
            temp = int((int(fan['coolingIdealTemp']) / 100.0) * 9 / 5) + 32
            dev.updateStateOnServer('coolingIdealTemp', temp)

            temp = int((int(fan['sleepIdealTemp']) / 100.0) * 9 / 5) + 32
            dev.updateStateOnServer('sleepIdealTemp', temp)

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
    def validateActionConfigUi(self, valuesDict, typeId, deviceId):
        self.DebugMsg(u"validating config for %s:%s" % (deviceId, typeId))
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
        elif typeId == 'fanSmartSleepIdealTemp':
            try:
                dev = indigo.devices[deviceId]
                tempUnits = dev.pluginProps['fanTempUnits']

                if tempUnits == 'C':
                    i = float(valuesDict['sleepTemp'])
                    if i < 10.0 or i > 32.0:
                        errorDict = indigo.Dict()
                        errorDict["sleepTemp"] = "Temperature must be between 10 and 32 degrees Celsius"
                        return (False, valuesDict, errorDict)
                    elif (i * 10.0) % 5 != 0.0:
                        errorDict = indigo.Dict()
                        errorDict["sleepTemp"] = "Temperature must be in 0.5 degree increments Celsius"
                        return (False, valuesDict, errorDict)
                    else:
                        return True
                else: # tempUnits == 'F'
                    i = int(valuesDict['sleepTemp'])
                    if i < 50 or i > 90:
                        errorDict = indigo.Dict()
                        errorDict["sleepTemp"] = "Temperature must be between 50 and 90 degrees Farenheight"
                        return (False, valuesDict, errorDict)
                    else:
                        return True
            except:
                errorDict = indigo.Dict()
                if tempUnits == 'C':
                    errorDict["sleepTemp"] = "Temperature must be a number"
                else:
                    errorDict["sleepTemp"] = "Temperature must be an integer"
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
            sock.sendto(cmd + '\n', (fanIP, 31415))

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

    ########################################
    def setFanWhooshModeOn(self, action):
        dev = indigo.devices[action.deviceId]
        fan = self.allfans[dev.id]

        f = fan['MAC']
        if f == '':
            f = dev.pluginProps['fanName']

        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;FAN;WHOOSH;ON>" % ( f )

        self.DebugMsg("Sending %s" % ( msg ))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setFanWhooshModeOff(self, action):
        dev = indigo.devices[action.deviceId]
        fan = self.allfans[dev.id]

        f = fan['MAC']
        if f == '':
            f = dev.pluginProps['fanName']

        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;FAN;WHOOSH;OFF>" % ( f )

        self.DebugMsg("Sending %s" % ( msg ))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setFanDirectionForward(self, action):
        dev = indigo.devices[action.deviceId]
        fan = self.allfans[dev.id]

        f = fan['MAC']
        if f == '':
            f = dev.pluginProps['fanName']

        if f['fan_level'] != '0':
            indigo.server.log("unable to set fan direction while fan is in motion", isError=True)
            return

        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;FAN;DIR;SET;FWD>" % ( f )

        self.DebugMsg("Sending %s" % ( msg ))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setFanDirectionReverse(self, action):
        dev = indigo.devices[action.deviceId]
        fan = self.allfans[dev.id]

        f = fan['MAC']
        if f == '':
            f = dev.pluginProps['fanName']

        if f['fan_level'] != '0':
            indigo.server.log("unable to set fan direction while fan is in motion", isError=True)
            return

        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;FAN;DIR;SET;REV>" % ( f )

        self.DebugMsg("Sending %s" % ( msg ))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setFanIndicatorsOn(self, action):
        dev = indigo.devices[action.deviceId]
        fan = self.allfans[dev.id]

        f = fan['MAC']
        if f == '':
            f = dev.pluginProps['fanName']

        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;DEVICE;INDICATORS;ON>" % ( f )

        self.DebugMsg("Sending %s" % ( msg ))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setFanIndicatorsOff(self, action):
        dev = indigo.devices[action.deviceId]
        fan = self.allfans[dev.id]

        f = fan['MAC']
        if f == '':
            f = dev.pluginProps['fanName']

        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;DEVICE;INDICATORS;OFF>" % ( f )

        self.DebugMsg("Sending %s" % ( msg ))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setFanBeepOn(self, action):
        dev = indigo.devices[action.deviceId]
        fan = self.allfans[dev.id]

        f = fan['MAC']
        if f == '':
            f = dev.pluginProps['fanName']

        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;DEVICE;BEEPER;ON>" % ( f )

        self.DebugMsg("Sending %s" % ( msg ))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setFanBeepOff(self, action):
        dev = indigo.devices[action.deviceId]
        fan = self.allfans[dev.id]

        f = fan['MAC']
        if f == '':
            f = dev.pluginProps['fanName']

        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;DEVICE;BEEPER;OFF>" % ( f )

        self.DebugMsg("Sending %s" % ( msg ))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setFanSmartCoolingIdealTemp(self, action):
        dev = indigo.devices[action.deviceId]

        temp = action.props.get("coolingTemp")
        tempUnits = dev.pluginProps['fanTempUnits']

        if tempUnits == 'C':
            newTemp = int(float(temp) * 100.0)
        else:
            newTemp = int((float((int(temp) - 32) * 5) / 9) * 100)

        self.DebugMsg(u"set cooling temperature to %s %s (%s)" % (str(temp), tempUnits, str(newTemp)))

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;LEARN;ZEROTEMP;SET;%s>" % (fanName, str(newTemp))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setFanSmartSleepIdealTemp(self, action):
        dev = indigo.devices[action.deviceId]

        temp = action.props.get("sleepTemp")
        tempUnits = dev.pluginProps['fanTempUnits']

        if tempUnits == 'C':
            newTemp = int(float(temp) * 100.0)
        else:
            newTemp = int((float((int(temp) - 32) * 5) / 9) * 100)

        self.DebugMsg(u"set sleep temperature to %s %s (%s)" % (str(temp), tempUnits, str(newTemp)))

        fanName = dev.pluginProps['fanName']
        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;SMARTSLEEP;IDEALTEMP;SET;%s>" % (fanName, str(newTemp))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setFanSleepModeOn(self, action):
        dev = indigo.devices[action.deviceId]
        fan = self.allfans[dev.id]

        f = fan['MAC']
        if f == '':
            f = dev.pluginProps['fanName']

        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;SLEEP;STATE;ON>" % ( f )

        self.DebugMsg("Sending %s" % ( msg ))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))

    ########################################
    def setFanSleepModeOff(self, action):
        dev = indigo.devices[action.deviceId]
        fan = self.allfans[dev.id]

        f = fan['MAC']
        if f == '':
            f = dev.pluginProps['fanName']

        fanIP = dev.pluginProps['fanIP']
        msg = "<%s;SLEEP;STATE;OFF>" % ( f )

        self.DebugMsg("Sending %s" % ( msg ))

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(msg, (fanIP, 31415))
