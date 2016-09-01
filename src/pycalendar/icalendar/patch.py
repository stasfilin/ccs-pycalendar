##
#    Copyright (c) 2015 Cyrus Daboo. All rights reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.
##

from calendar import Calendar
from pycalendar.componentbase import ComponentBase
from pycalendar.datetime import DateTime
from pycalendar.icalendar import definitions
from pycalendar.icalendar.component import Component
from pycalendar.icalendar.componentrecur import ComponentRecur
from pycalendar.icalendar.property import Property
from pycalendar.icalendar.vpatch import VPatch
from pycalendar.icalendar.vpatchcreate import Create
from urlparse import unquote
import operator
from pycalendar.icalendar.vpatchupdate import Update


class PatchDocument(object):
    """
    Represents an entire patch document by maintaining a list of all its commands.
    """

    def __init__(self, calendar=None):
        self.commands = []
        if calendar is not None:
            self.parsePatch(calendar)

    def parsePatch(self, calendar):
        """
        Parse an iCalendar object and extract all the VPATCH components in the
        proper order and parse them as a set of commands to use when applying
        the patch.

        @param calendar: iCalendar object to parse
        @type calendar: L{Calendar}
        """

        # Get all VPATCH components
        vpatches = calendar.getComponents(definitions.cICalComponent_VPATCH)

        # Sort
        def _vpatchOrder(component):
            return component.loadValueInteger(definitions.cICalProperty_PATCH_ORDER)
        vpatches = sorted(vpatches, key=_vpatchOrder)

        # Extract commands from each VPATCH
        for vpatch in vpatches:
            for component in vpatch.getComponents():
                if component.getType().upper() not in (definitions.cICalComponent_CREATE, definitions.cICalComponent_UPDATE, definitions.cICalComponent_DELETE,):
                    raise ValueError("Invalid component in VPATCH: {}".format(component.getType().upper()))
                commands = Command.parseFromComponent(component)
                self.commands.extend(commands)

        # Validate
        self.validate()

    def validate(self):
        """
        Validate all the commands.
        """
        for command in self.commands:
            command.validate()

    def applyPatch(self, calendar):
        """
        Apply the patch to the specified calendar. The supplied L{Calendar} object will be
        changed in place.

        @param calendar: calendar to patch
        @type calendar: L{Calendar}
        """
        for command in self.commands:
            command.applyPatch(calendar)


class Command(object):
    """
    Represents a patch document command.
    """

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    ADD = "add"
    REMOVE = "remove"
    ACTIONS = (CREATE, UPDATE, DELETE, ADD, REMOVE)

    componentToAction = {
        definitions.cICalComponent_CREATE: CREATE,
        definitions.cICalComponent_UPDATE: UPDATE,
        definitions.cICalComponent_DELETE: DELETE,
    }

    def __init__(self):
        self.action = None
        self.path = None
        self.data = None

    @classmethod
    def create(cls, action, path, data=None):
        if action not in cls.ACTIONS:
            raise ValueError("Invalid action: {}".format(action))
        if isinstance(path, str):
            path = Path(path)
        elif not isinstance(path, Path):
            raise ValueError("Invalid path: {}".format(path))
        if data is not None and not isinstance(data, Component):
            raise ValueError("Invalid data: {}".format(data))
        if action == Command.DELETE:
            if data is not None:
                raise ValueError("Must not have data for action: {}".format(action))
        else:
            if data is None:
                raise ValueError("Must have data for action: {}".format(action))

        command = Command()
        command.action = action
        command.path = path
        command.data = data
        return command

    @classmethod
    def parseFromComponent(cls, component):
        """
        Parse a command from a list of text format lines.

        @param component: ADD/UPDATE/REMOVE component to process.
        @type component: L{Component}

        @return: L{Command} if a command was parsed, L{None} if not
        """

        # Get the action from the component type
        action = cls.componentToAction.get(component.getType().upper())
        if action not in cls.ACTIONS:
            raise ValueError("Invalid component: {}".format(component.getType().upper()))

        # DELETE action can have multiple TARGETs - we will treat each of those
        # as a separate command Get the path from the TARGET property
        if action == Command.DELETE:
            if len(component.getComponents()) != 0:
                raise ValueError("No components allowed in DELETE")
            targets = component.getProperties()
            if definitions.cICalProperty_TARGET not in targets:
                raise ValueError("Missing TARGET properties in component: {}".format(component.getType().upper()))
            if len(targets) > 1:
                raise ValueError("Only TARGET properties allowed in component: {}".format(component.getType().upper()))
            try:
                return [Command.create(action, Path(target.getTextValue().getValue()), None) for target in targets[definitions.cICalProperty_TARGET]]
            except ValueError:
                raise ValueError("Invalid target path: {}".format(targets))
        else:
            target = component.getPropertyString(definitions.cICalProperty_TARGET)
            if target is None:
                raise ValueError("Missing TARGET property in component: {}".format(component.getType().upper()))
            try:
                path = Path(target)
            except ValueError:
                raise ValueError("Invalid target path: {}".format(target))
            data = component.duplicate()
            data.removeProperties(definitions.cICalProperty_TARGET)
            return (Command.create(action, path, data), )

        return Command.create(action, path, data)

    def validate(self):
        """
        Make sure the semantics of the patch are correct based on the supplied data etc.
        """

        # Validation depends on the action
        if self.action == Command.CREATE:
            if self.path.targetComponent():
                # Data must be one or more components only
                if len(self.data.getProperties()) + len(self.data.getComponents()) == 0:
                    raise ValueError("create action for components must include at least one property or component: {}".format(self.path))

            elif self.path.targetProperty():
                # Data must be one or more SETPARAMETER properties only
                if len(self.data.getComponents()) != 0:
                    raise ValueError("create action for parameters must not include components: {}".format(self.path))
                if len(self.data.getProperties()) != 1 or definitions.cICalProperty_SETPARAMETER not in self.data.getProperties():
                    raise ValueError("create action for parameters must have only one or more SETPARAMETER properties: {}".format(self.path))

            else:
                raise ValueError("create action path is not valid: {}".format(self.path))

        elif self.action == Command.UPDATE:
            if self.path.targetComponent():
                # Data must be one or more components only
                if len(self.data.getProperties()) != 0:
                    raise ValueError("update action for components must not include properties: {}".format(self.path))
                if len(self.data.getComponents()) == 0:
                    raise ValueError("update action for components must have at least one component: {}".format(self.path))

                # Data components must match component being replaced
                componentNames = set([component.getType() for component in self.data.getComponents()])
                if len(componentNames) > 1:
                    raise ValueError("update action for components must have components of the same type: {}".format(self.path))
                if list(componentNames)[0] != self.path.components[-1].name:
                    raise ValueError("update action for components must have components with matching type: {}".format(self.path))

            elif self.path.targetPropertyNoName():
                # Data must be one or more properties only
                if len(self.data.getComponents()) != 0:
                    raise ValueError("update action for properties must not include components: {}".format(self.path))
                if len(self.data.getProperties()) == 0:
                    raise ValueError("update action for properties must have at least one property: {}".format(self.path))

            elif self.path.targetProperty():
                # Data must be one or more properties only
                if len(self.data.getComponents()) != 0:
                    raise ValueError("update action for properties must not include components: {}".format(self.path))
                if len(self.data.getProperties()) == 0:
                    raise ValueError("update action for properties must have at least one property: {}".format(self.path))

                # Data properties must match property being replaced
                propertyNames = set(self.data.getProperties().keys())
                if len(propertyNames) > 1:
                    raise ValueError("update action for specific properties must have properties of the same type: {}".format(self.path))
                if list(propertyNames)[0] != self.path.property.name:
                    raise ValueError("update action for specific properties must have properties with matching type: {}".format(self.path))

            elif self.path.targetParameterNoName():
                # Data must be one or more SETPARAMETER properties only
                if len(self.data.getComponents()) != 0:
                    raise ValueError("update action for parameters must not include components: {}".format(self.path))
                if len(self.data.getProperties()) != 1 or definitions.cICalProperty_SETPARAMETER not in self.data.getProperties():
                    raise ValueError("update action for parameters must have only one or more SETPARAMETER properties: {}".format(self.path))
            else:
                raise ValueError("update action path is not valid: {}".format(self.path))

        elif self.action == Command.DELETE:
            if self.path.targetComponent() or self.path.targetProperty() or self.path.targetParameter():
                # Must not be any data at all
                if self.data is not None:
                    raise ValueError("delete action cannot have data: {}".format(self.path))
            else:
                raise ValueError("update action path is not valid: {}".format(self.path))

    def applyPatch(self, calendar):
        """
        Apply the patch to the specified calendar. The supplied L{Calendar} object will be
        changed in place.

        @param calendar: calendar to patch
        @type calendar: L{Calendar}
        """
        matching_items = self.path.match(calendar, for_update=(self.action == Command.UPDATE))
        call = getattr(self, "{}Action".format(self.action))
        if call is not None:
            call(matching_items)

    def createAction(self, matches):
        """
        Execute a create action on the matched items.

        @param matches: list of matched components/properties/parameters
        @type matches: L{list}
        """
        if self.path.targetComponent():
            # Data is a list of components or properties
            for component in matches:
                for newcomponent in self.data.getComponents():
                    component.addComponent(newcomponent.duplicate())
                for newpropertylist in self.data.getProperties().values():
                    for newproperty in newpropertylist:
                        component.addProperty(newproperty.duplicate())

        elif self.path.targetProperty():
            # Now add new parameters (from the data) to each parent property
            setParameter = self.data.getProperties(definitions.cICalProperty_SETPARAMETER)
            for _ignore_component, property in matches:
                for parameters in setParameter[0].getParameters().values():
                    # Remove existing, then add
                    property.removeParameters(parameters[0].getName())
                    property.addParameter(parameters[0].duplicate())

        else:
            raise ValueError("create action path is not valid: {}".format(self.path))

    def updateAction(self, matches):
        """
        Execute an update action on the matched items.

        @param matches: list of matched components/properties/parameters
        @type matches: L{list}
        """

        if self.path.targetComponent():
            # First remove matched components and record the parent
            parent = None
            for component in matches:
                parent = component.getParentComponent()
                component.removeFromParent()

            # Now add new components (from the data) to the parent
            if parent is not None:
                for component in matches:
                    for newcomponent in self.data.getComponents():
                        parent.addComponent(newcomponent.duplicate())

        elif self.path.targetPropertyNoName():
            # First remove properties from matched components
            propnames = self.data.getProperties().keys()
            for component in matches:
                for propname in propnames:
                    component.removeProperties(propname)

            # Add properties to matched components
            for component in matches:
                for newpropertylist in self.data.getProperties().values():
                    for newproperty in newpropertylist:
                        component.addProperty(newproperty.duplicate())

        elif self.path.targetProperty():
            # First remove matched properties and record the parent components
            components = set()
            for component, property in matches:
                components.add(component)
                if property is not None:
                    component.removeProperty(property)

            # Now add new properties (from the data) to each parent component
            for component in components:
                for newpropertylist in self.data.getProperties().values():
                    for newproperty in newpropertylist:
                        component.addProperty(newproperty.duplicate())

        elif self.path.targetParameterNoName():
            # Add/replace new parameters (from the data) to each parent property
            setParameter = self.data.getProperties(definitions.cICalProperty_SETPARAMETER)
            for _ignore_component, property, _ignore_parameter_name in matches:
                for parameters in setParameter[0].getParameters().values():
                    # Remove existing, then add
                    if property.hasParameter(parameters[0].getName()):
                        property.removeParameters(parameters[0].getName())
                    property.addParameter(parameters[0].duplicate())
        else:
            raise ValueError("update action path is not valid: {}".format(self.path))

    def deleteAction(self, matches):
        """
        Execute a delete action on the matched items.

        @param matches: list of matched components/properties/parameters
        @type matches: L{list}
        """
        if self.path.targetComponent():
            for component in matches:
                component.removeFromParent()

        elif self.path.targetProperty():
            for component, property in matches:
                component.removeProperty(property)

        elif self.path.targetParameter():
            for _ignore_component, property, parameter_name in matches:
                property.removeParameters(parameter_name)
        else:
            raise ValueError("delete action path is not valid: {}".format(self.path))

    def addAction(self, matches):
        pass

    def removeAction(self, matches):
        pass


class Path(object):
    """
    A path item used to select one or more iCalendar elements
    """

    def __init__(self, path):
        """
        Create a L{Path} by parsing a text path.

        @param path: the path to parse
        @type path: L{str}
        """
        self.components = []
        self.property = None
        self.parameter = None
        self._parsePath(path)

    def __str__(self):
        path = "".join(map(str, self.components))
        if self.property:
            path += str(self.property)
            if self.parameter:
                path += str(self.parameter)
        return path

    def targetComponent(self):
        """
        Indicate whether the path targets a component.

        @return: L{True} for a component target, L{False} otherwise.
        @rtype: L{bool}
        """
        return self.property is None

    def targetProperty(self):
        """
        Indicate whether the path targets a property.

        @return: L{True} for a property target, L{False} otherwise.
        @rtype: L{bool}
        """
        return (
            self.property is not None and
            not self.property.noName() and
            self.parameter is None
        )

    def targetPropertyNoName(self):
        """
        Indicate whether the path targets a property.

        @return: L{True} for a property target, L{False} otherwise.
        @rtype: L{bool}
        """
        return self.property is not None and self.property.noName()

    def targetParameter(self):
        """
        Indicate whether the path targets a parameter.

        @return: L{True} for a parameter target, L{False} otherwise.
        @rtype: L{bool}
        """
        return (
            self.property is not None and
            self.parameter is not None and
            not self.parameter.noName()
        )

    def targetParameterNoName(self):
        """
        Indicate whether the path targets a parameter.

        @return: L{True} for a parameter target, L{False} otherwise.
        @rtype: L{bool}
        """
        return (
            self.property is not None and
            self.parameter is not None and
            self.parameter.noName()
        )

    def _parsePath(self, path):
        """
        Parse a text path into its constituent segments.

        @param path: the path to parse
        @type path: L{str}
        """

        segments = path.split("/")
        property_segment = None
        parameter_segment = None
        if segments[0] != "":
            raise ValueError("Invalid path: {}".format(path))
        del segments[0]
        if "#" in segments[-1]:
            segments[-1], property_segment = segments[-1].split("#", 1)
            if ";" in property_segment:
                property_segment, parameter_segment = property_segment.split(";", 1)

        for item in range(len(segments)):
            self.components.append(Path.ComponentSegment(segments[item]))
        if property_segment is not None:
            self.property = Path.PropertySegment(property_segment)
        if parameter_segment is not None:
            self.parameter = Path.ParameterSegment(parameter_segment)

    class ComponentSegment(object):
        """
        Represents a component segment of an L{Path}.
        """

        def __init__(self, segment):
            """
            Create a component segment of a path by parsing the text.

            @param path: the segment to parse
            @type path: L{str}
            """
            self.name = None
            self.uid = None
            self.rid = None
            self.rid_value = None

            self._parseSegment(segment)

        def __str__(self):
            path = "/" + self.name
            if self.uid:
                path += "[UID={}]".format(self.uid)
            if self.rid:
                path += "[RID={}]".format(self.rid_value if self.rid_value is not None else "M")
            return path

        def __repr__(self):
            return "<ComponentSegment: {name}[{uid}][{rid}]".format(
                name=self.name,
                uid=self.uid,
                rid=(self.rid_value if self.rid_value is not None else "M") if self.rid else None
            )

        def __eq__(self, other):
            return (self.name == other.name) and \
                (self.uid == other.uid) and \
                (self.rid == other.rid) and \
                (self.rid_value == other.rid_value)

        def _parseSegment(self, segment):
            """
            Parse a component segment of a path into its constituent parts.

            @param path: the segment to parse
            @type path: L{str}
            """
            pos = segment.find("[")
            if pos != -1:
                self.name, segment_rest = segment.split("[", 1)
                segments = segment_rest.split("[")
                if segments[0].startswith("UID=") and segments[0][-1] == "]":
                    self.uid = unquote(segments[0][4:-1])
                    del segments[0]
                if segments and segments[0].startswith("RID=") and segments[0][-1] == "]":
                    rid = unquote(segments[0][4:-1])
                    if rid == "M":
                        self.rid_value = None
                    else:
                        try:
                            self.rid_value = DateTime.parseText(rid) if rid else None
                        except ValueError:
                            raise ValueError("Invalid component match {}".format(segment))
                    self.rid = True
                    del segments[0]

                if segments:
                    raise ValueError("Invalid component match {}".format(segment))
            else:
                self.name = segment

            self.name = self.name.upper()

        def match(self, items):
            """
            Returns all sub-components of the components passed in via the L{items} list
            that match this path.

            @param items: calendar items to match
            @type items: L{list}

            @return: items matched
            @rtype: L{list}
            """

            results = []
            for item in items:
                assert(isinstance(item, ComponentBase))
                matches = item.getComponents(self.name)
                if self.uid and matches:
                    matches = [item for item in matches if item.getUID() == self.uid]
                if self.rid and matches:
                    # self.rid is None if no RID= appears in the path.
                    # self.rid_value is None if RID= appears with no value - match the master instance
                    # Otherwise match the specific self.rid value.
                    rid_matches = [item for item in matches if isinstance(item, ComponentRecur) and item.getRecurrenceID() == self.rid_value]
                    if len(rid_matches) == 0:
                        if self.rid_value:
                            # Try deriving an instance - fail if cannot
                            # Need to have the master first
                            masters = [item for item in matches if isinstance(item, ComponentRecur) and item.getRecurrenceID() is None]
                            if not masters:
                                raise ValueError("No master component for path {}".format(self))
                            elif len(masters) > 1:
                                raise ValueError("Too many master components for path {}".format(self))
                            derived = masters[0].deriveComponent(self.rid_value)
                            masters[0].getParentComponent().addComponent(derived)
                            rid_matches.append(derived)
                    matches = rid_matches
                results.extend(matches)

            return results

    class PropertySegment(object):
        """
        Represents a property segment of an L{Path}.
        """

        def __init__(self, segment):
            """
            Create a property segment of a path by parsing the text.

            @param path: the segment to parse
            @type path: L{str}
            """
            self.name = None
            self.matchCondition = None
            self._parseSegment(segment)

        def __str__(self):
            path = "#" + self.name
            if self.matchCondition:
                path += "[{}{}]".format("=" if self.matchCondition[1] == operator.eq else "!", self.matchCondition[0])
            return path

        def __repr__(self):
            return "<PropertySegment: {s.name}[{s.matchCondition}]".format(s=self)

        def __eq__(self, other):
            return (self.name == other.name) and \
                (self.matchCondition == other.matchCondition)

        def _parseSegment(self, segment):
            """
            Parse a property segment of a path into its constituent parts.

            @param path: the segment to parse
            @type path: L{str}
            """
            if "[" in segment:
                self.name, segment_rest = segment.split("[", 1)
                matches = segment_rest.split("[")
                if len(matches) != 1:
                    raise ValueError("Invalid property match {}".format(segment))
                if matches[0][-1] != "]" or len(matches[0]) < 4:
                    raise ValueError("Invalid property match {}".format(segment))
                if matches[0][0] == "=":
                    op = operator.eq
                elif matches[0][0] == "!":
                    op = operator.ne
                else:
                    raise ValueError("Invalid property match {}".format(segment))
                self.matchCondition = (unquote(matches[0][1:-1]), op,)
            else:
                self.name = segment

        def noName(self):
            return self.name == ""

        def match(self, components, for_update):
            """
            Returns all properties of the components passed in via the L{items} list
            that match this path.

            @param components: components to match
            @type components: L{list}

            @return: items matched
            @rtype: L{list}
            """

            # Empty name is used for create
            if self.name:
                results = []
                for component in components:
                    assert(isinstance(component, ComponentBase))
                    if self.matchCondition is not None:
                        matches = [(component, prop,) for prop in component.getProperties(self.name) if self.matchCondition[1](prop.getValue().getTextValue(), self.matchCondition[0])]
                    else:
                        matches = [(component, prop,) for prop in component.getProperties(self.name)]
                        if len(matches) == 0 and for_update:
                            # If no property exists, return L{None} so that an update action will add one
                            matches = [(component, None)]
                    results.extend(matches)
            else:
                results = [(component, None,) for component in components]

            return results

    class ParameterSegment(object):
        """
        Represents a parameter segment of an L{Path}.
        """

        def __init__(self, segment):
            """
            Create a parameter segment of a path by parsing the text.

            @param path: the segment to parse
            @type path: L{str}
            """
            self.name = None
            self._parseSegment(segment)

        def __str__(self):
            path = ";" + self.name
            return path

        def __repr__(self):
            return "<ParameterSegment: {s.name}".format(s=self)

        def __eq__(self, other):
            return (self.name == other.name)

        def _parseSegment(self, segment):
            """
            Parse a parameter segment of a path into its constituent parts.

            @param path: the segment to parse
            @type path: L{str}
            """
            if "[" in segment:
                raise ValueError("Invalid parameter segment {}".format(segment))
            else:
                self.name = segment

        def noName(self):
            return self.name == ""

        def match(self, properties):
            """
            Returns all properties of the components passed in via the L{items} list
            that match this path, together with the parameter name being targeted.

            @param properties: properties to match
            @type properties: L{list}

            @return: items matched
            @rtype: L{list}
            """

            # Empty name is used for create
            if self.name:
                results = []
                for component, property in properties:
                    assert(isinstance(component, ComponentBase))
                    assert(isinstance(property, Property))
                    results.append((component, property, self.name,))
            else:
                results = [(component, property, None,) for component, property in properties]

            return results

    def match(self, calendar, for_update=False):
        """
        Return the list of matching items in the specified calendar.

        @param calendar: calendar to match
        @type calendar: L{Calendar}
        @param for_update: L{True} if a property match should return an empty
            result when there is no match item and no matching property
        @type for_update: L{bool}

        @return: items matched
        @rtype: L{list}
        """

        # First segment of path is always assumed to be VCALENDAR - we double check that
        if self.components[0].name != "VCALENDAR" or calendar.getType().upper() != "VCALENDAR":
            return []

        # Start with the VCALENDAR object as the initial match
        results = [calendar]
        for component_segment in self.components[1:]:
            results = component_segment.match(results)

        if self.property is not None and not self.property.noName():
            results = self.property.match(results, for_update)
            if self.parameter is not None:
                results = self.parameter.match(results)

        return results


class PatchGenerator(object):
    """
    Class that manages the creation of a VPATCH by diff'ing two components.
    """

    @staticmethod
    def createPatch(oldcalendar, newcalendar):
        """
        Create a patch iCelendar object for the difference between
        L{oldcalendar} and L{newcalendar}.

        @param oldcalendar: the old calendar data
        @type oldcalendar: L{Calendar}
        @param newcalendar: thew new calendar data
        @type newcalendar: L{Calendar}
        """

        # Create the VPATCH object
        patch = Calendar()
        patch.addDefaultProperties()
        vpatch = VPatch(parent=patch)
        vpatch.addDefaultProperties()
        patch.addComponent(vpatch)

        # Recursively traverse the differences between two components, adding
        # appropriate items to the VPATCH to describe the differences
        PatchGenerator.diffComponents(oldcalendar, newcalendar, vpatch, "")

    @staticmethod
    def diffComponents(oldcomponent, newcomponent, vpatch, path):
        """
        Recursively traverse the differences between two components, adding
        appropriate items to the VPATCH to describe the differences.

        @param oldcomponent: the old component
        @type oldcomponent: L{Component}
        @param newcomponent: the new component
        @type newcomponent: L{Component}
        @param vpatch: the patch to use
        @type vpatch: L{VPatch}
        """

        # Process properties then sub-components
        PatchGenerator.processProperties(oldcomponent, newcomponent, vpatch, path)
        PatchGenerator.processSubComponents(oldcomponent, newcomponent, vpatch, path)

    @staticmethod
    def processProperties(oldcomponent, newcomponent, vpatch, path):
        """
        Determine the property differences between two components and create
        appropriate VPATCH entries.

        @param oldcomponent: the old component
        @type oldcomponent: L{Component}
        @param newcomponent: the new component
        @type newcomponent: L{Component}
        @param vpatch: the patch to use
        @type vpatch: L{VPatch}
        """

        # Update path to include this component
        path += "/{}".format(oldcomponent.getType())

        # Use two way set difference to find new and removed
        oldset = set(oldcomponent.getProperties().keys())
        newset = set(newcomponent.getProperties().keys())

        # Create and Update patch components can aggregate changes in some cases, so we will have some common objects for those
        patchComponent = {
            "create": None,
            "update": None,
            "delete": None,
        }

        def _getPatchComponent(ptype):
            if patchComponent[ptype] is None:
                if ptype == "create":
                    # Use CREATE component in VPATCH
                    patchComponent[ptype] = Create(parent=vpatch)
                    patchComponent[ptype].addProperty(Property(definitions.cICalProperty_TARGET, path))
                    vpatch.addComponent(patchComponent[ptype])
                elif ptype == "update":
                    # Use UPDATE component in VPATCH
                    patchComponent[ptype] = Update(parent=vpatch)
                    patchComponent[ptype].addProperty(Property(definitions.cICalProperty_TARGET, "{}#".format(path)))
                    vpatch.addComponent(patchComponent[ptype])
                elif ptype == "delete":
                    # Use UPDATE component in VPATCH
                    patchComponent[ptype] = vpatch.getDeleteComponent()
            return patchComponent[ptype]

        # New ones
        newpropnames = newset - oldset
        if len(newpropnames) != 0:
            # Add each property to CREATE
            for newpropname in newpropnames:
                for prop in newcomponent.getProperties(newpropname):
                    _getPatchComponent("create").addProperty(prop.duplicate())

        # Removed ones
        oldpropnames = oldset - newset
        if len(oldpropnames) != 0:
            # Add each property to DELETE
            for oldpropname in oldpropnames:
                _getPatchComponent("delete").addProperty(Property(definitions.cICalProperty_TARGET, "{}#{}".format(path, oldpropname)))

        # Ones that exist in both old and new: this is tricky as we now need to find out what is different.
        # We handle two cases: single occurring properties vs multi-occurring
        checkpropnames = newset & oldset
        for propname in checkpropnames:
            oldprops = oldcomponent.getProperties(propname)
            newprops = newcomponent.getProperties(propname)

            # Look for singletons
            if len(oldprops) == 1 and len(newprops) == 1:
                # Check for difference
                if oldprops[0] != newprops[0]:
                    _getPatchComponent("update").addProperty(newprops[0].duplicate())

            # Rest are multi-occurring
            else:
                # Removes ones that are exactly the same
                oldset = set(oldprops)
                newset = set(newprops)
                oldsetchanged = oldset - newset
                newsetchanged = newset - oldset

                # Need to check for ones that have the same value, but different parameters
                oldvalues = dict([(prop.getValue().getTextValue(), prop) for prop in oldsetchanged])
                newvalues = dict([(prop.getValue().getTextValue(), prop) for prop in newsetchanged])

                # Ones to remove by value (ones whose value only exists in the old set)
                for removeval in set(oldvalues.keys()) - set(newvalues.keys()):
                    _getPatchComponent("delete").addProperty(Property(definitions.cICalProperty_TARGET, "{}#{}[={}]".format(path, propname, removeval)))

                # Ones to create (ones whose value only exists in the new set)
                for createval in set(newvalues.keys()) - set(oldvalues.keys()):
                    _getPatchComponent("create").addProperty(newvalues[createval].duplicate())

                # Ones with the same value - check if parameters are different
                for sameval in set(oldvalues.keys()) & set(newvalues.keys()):
                    oldprop = oldvalues[sameval]
                    newprop = newvalues[sameval]
                    if oldprop != newprop:
                        # Use UPDATE component targeting property value in VPATCH
                        update = Update(parent=vpatch)
                        update.addProperty(Property(definitions.cICalProperty_TARGET, "{}#{}[={}]".format(path, propname, sameval)))
                        update.addProperty(newprop.duplicate())
                        vpatch.addComponent(update)

    @staticmethod
    def processSubComponents(oldcomponent, newcomponent, vpatch, path):
        """
        Determine the sub-component differences between two components and create
        appropriate VPATCH entries.

        @param oldcomponent: the old component
        @type oldcomponent: L{Component}
        @param newcomponent: the new component
        @type newcomponent: L{Component}
        @param vpatch: the patch to use
        @type vpatch: L{VPatch}
        """

        # Update path to include this component
        path += "/{}".format(oldcomponent.getType())

        # Use two way set difference to find new and removed
        oldmap = {}
        for component in oldcomponent.getComponents():
            oldmap.setdefault(component.getType(), []).append(component)
        newmap = {}
        for component in newcomponent.getComponents():
            newmap.setdefault(component.getType(), []).append(component)

        oldset = set(oldmap.keys())
        newset = set(newmap.keys())

        # Create and Update patch components can aggregate changes in some cases, so we will have some common objects for those
        patchComponent = {
            "create": None,
            "update": None,
            "delete": None,
        }

        def _getPatchComponent(ptype):
            if patchComponent[ptype] is None:
                if ptype == "create":
                    # Use CREATE component in VPATCH
                    patchComponent[ptype] = Create(parent=vpatch)
                    patchComponent[ptype].addProperty(Property(definitions.cICalProperty_TARGET, path))
                    vpatch.addComponent(patchComponent[ptype])
                elif ptype == "update":
                    # Use UPDATE component in VPATCH
                    patchComponent[ptype] = Update(parent=vpatch)
                    patchComponent[ptype].addProperty(Property(definitions.cICalProperty_TARGET, path))
                    vpatch.addComponent(patchComponent[ptype])
                elif ptype == "delete":
                    # Use UPDATE component in VPATCH
                    patchComponent[ptype] = vpatch.getDeleteComponent()
            return patchComponent[ptype]

        # New ones
        newcompnames = newset - oldset
        if len(newcompnames) != 0:
            # Add each component to CREATE
            for newcompname in newcompnames:
                for comp in newmap[newcompname]:
                    _getPatchComponent("create").addComponent(comp.duplicate(parent=_getPatchComponent("create")))

        # Removed ones
        oldcompnames = oldset - newset
        if len(oldcompnames) != 0:
            # Add each component to DELETE
            for oldcompname in oldcompnames:
                _getPatchComponent("delete").addProperty(Property(definitions.cICalProperty_TARGET, "{}/{}".format(path, oldcompname)))

        # Ones that exist in both old and new: this is tricky as we now need to find out what is different.
        # We handle two cases: single occurring properties vs multi-occurring
        checkcompnames = newset & oldset
        for compname in checkcompnames:
            oldcomps = oldcomponent.getComponents(compname)
            newcomps = newcomponent.getComponents(compname)

            # Look for singletons
            if len(oldcomps) == 1 and len(newcomps) == 1:
                # Check for difference
                if oldcomps[0] != newcomps[0]:
                    uid = oldcomps[0].getUID()
                    rid = oldcomps[0].getRecurrenceID() if isinstance(oldcomps[0], ComponentRecur) else None
                    upath = "{}/{}[UID={}]".format(path, compname, uid)
                    if rid is not None:
                        upath = "{}[RID={}]".format(upath, str(rid))
                    # Use UPDATE component targeting component UID in VPATCH
                    update = Update(parent=vpatch)
                    update.addProperty(Property(definitions.cICalProperty_TARGET, upath))
                    update.addComponent(newcomps[0].duplicate())
                    vpatch.addComponent(update)