#!/usr/bin/python
#coding=utf-8

import graphviz
import docker

from collections import defaultdict
from enum import Enum
from typing import List, Dict

TRAEFIK_DEFAULT_PORT = '80/tcp'

'''
Describe all possibles elements in an architecture graph
'''
class GraphElement(Enum):
    def __repr__(self):
        return '<%s.%s>' % (self.__class__.__name__, self.name)

    TRAEFIK = 'Either the URL node or the edges between containers and URL node'
    PORT = 'Either a **host** port or the link between host port and container exposed port(s)'
    IMAGE = 'Cluster around the containers'
    LINK = 'Edge between two non-Traefik containers'
    CONTAINER = 'Concrete instance of an image'
    NETWORK = 'Cluster around containers of the same Docker network'
    VM = 'Virtual machine (host)'

'''
This class represents a Docker container with only useful members
for GraphBuilder.
'''
class ShortContainer:
    def __init__(self, name: str):
        self.name = name;
        self.image = str()
        self.ports = defaultdict(set)
        self.networks = set()
        self.links = set()

        self.__backend_port = TRAEFIK_DEFAULT_PORT
        self.__url = str()

    @property
    def backend_port(self):
        return self.__backend_port;

    @backend_port.setter
    def backend_port(self, value):
        if value is not None and '/' not in value:
            value = value + '/tcp'
        self.__backend_port = value

    @property
    def url(self):
        return self.__url;

    @url.setter
    def url(self, value):
        if value is not None:
            value = value.replace('Host:', '')
        self.__url = value

'''
This class asks a Docker daemon informations about running Containers
and constructs a graph showing dependencies between containers, images and ports.

We also use Traefik labels to show links between the reverse proxy and the containers.
'''
class GraphBuilder:
    @property
    def graph(self):
        if self.__graph is None:
            self.__build_graph()
        return self.__graph

    '''
    Constructor.

    :param docker_client : docker client used to build the graph
    :param color_scheme : colors used for the graph
    :param vm_name : name of the virtual machine
    :param vm_label : label to put on the virtual machine graph
    :param exclude : name of containers to exclude of the layout
    '''
    def __init__(self, docker_client: docker.DockerClient, color_scheme: Dict[str, str], vm_label: str, vm_name: str, exclude: List[str] = []):
        self.color_scheme = color_scheme
        self.docker_client = docker_client
        self.vm_label = vm_label
        self.vm_name = vm_name
        self.exclude = exclude
        self.has_traefik = False
        self.__graph = None

    '''
    Builds a Digraph object representing a single host.
    After running this function, the Digraph object is accessible
    via the __graph property
    '''
    def __build_graph(self):
        running = self.__get_containers()
        self.__graph = graphviz.Digraph(
            name = '{0}'.format(self.vm_label),
            comment = 'Virtual machine : {0}'.format(self.vm_label)
        )

        # Create a subgraph for the virtual machine
        with self.__graph.subgraph(name = 'cluster_{0}'.format(self.vm_label)) as vm:
            vm.attr(
                label = 'Virtual machine : {0}'.format(self.vm_label),
                **self.__get_style(GraphElement.VM)
            )

            # Group containers by networks
            network_dict = defaultdict(list)
            for c in running:
                for n in c.networks:
                    network_dict[n].append(c)

            # Add all running containers as a node in their own network subgraph
            for k, v in network_dict.items():
                with vm.subgraph(name = 'cluster_{0}'.format(self.__node_name(k))) as network:
                    network.attr(
                        label = 'Network : {0}'.format(k),
                        **self.__get_style(GraphElement.NETWORK)
                    )
                    for c in v:
                        with network.subgraph(name = 'cluster_{0}'.format(self.__node_name(c.image))) as image:
                            image.attr(
                                label = c.image,
                                **self.__get_style(GraphElement.IMAGE)
                            )
                            image.node(
                                self.__node_name(c.name),
                                self.__record_label(c.name, c.ports),
                                **self.__get_style(GraphElement.CONTAINER)
                            )
                        # Instead of using a link label (takes a lot of space), put a node without shape for the container's url
                        if self.has_traefik and c.url is not None:
                            network.node(
                                self.__node_name(c.url),
                                c.url,
                                **self.__get_style(GraphElement.TRAEFIK)
                            )

            for c in running:
                if self.has_traefik and c.url is not None:
                    # Edge from traefik default port to URL node
                    vm.edge(
                        self.__node_name(self.traefik_container, TRAEFIK_DEFAULT_PORT),
                        self.__node_name(c.url),
                        **self.__get_style(GraphElement.TRAEFIK)
                    )
                    # Edge from URL node to target container exposed port
                    vm.edge(
                        self.__node_name(c.url), self.__node_name(c.name, c.backend_port),
                        **self.__get_style(GraphElement.TRAEFIK)
                    )

                # Add links between containers
                for l in c.links:
                    vm.edge(
                        self.__node_name(c.name, c.name),
                        self.__node_name(l, l),
                        **self.__get_style(GraphElement.LINK)
                    )

                # Add port mapping between host and containers
                for expose, host_ports in c.ports.items():
                    for port in host_ports:
                        vm.node(
                            self.__node_name(port),
                            port,
                            **self.__get_style(GraphElement.PORT)
                        )
                        vm.edge(
                            self.__node_name(port),
                            self.__node_name(c.name, expose),
                            **self.__get_style(GraphElement.PORT)
                        )

    '''
    Returns a dictionary than can be unpacked to create a graph element (node, edge or cluster).
    This is a helper function, mainly used because setting the color each time is annoying.

    :param node_type : the part of the graph do we need to style
    :returns Dictionary containing the styling arguments
    :rtype Dict[str, str]
    '''
    def __get_style(self, graph_element: GraphElement):
        if graph_element == GraphElement.TRAEFIK:
            return {
                'arrowhead': "none",
                'color': self.color_scheme['traefik'],
                'fillcolor': self.color_scheme['traefik'],
                'fontcolor': self.color_scheme['bright_text']
            }
        elif graph_element == GraphElement.PORT:
            return {
                'shape': 'diamond',
                'fillcolor': self.color_scheme['port'],
                'fontcolor': self.color_scheme['bright_text']
            }
        elif graph_element == GraphElement.IMAGE:
            return {
                'style': 'filled,rounded',
                'color': self.color_scheme['image'],
                'fillcolor': self.color_scheme['image']
            }
        elif graph_element == GraphElement.LINK:
            return {
                'color': self.color_scheme['link']
            }
        elif graph_element == GraphElement.CONTAINER:
            return {
                'color': self.color_scheme['dark_text'],
                'fillcolor': self.color_scheme['container'],
                'fontcolor': self.color_scheme['dark_text']
            }
        elif graph_element == GraphElement.NETWORK:
            return {
                'style': 'filled,rounded',
                'color': self.color_scheme['network'],
                'fillcolor': self.color_scheme['network']
            }
        elif graph_element == GraphElement.VM:
            return {
                'style': 'filled,rounded',
                'fillcolor': self.color_scheme['vm']
            }
        else:
            raise Exception('Unkown graph element')

    '''
    As each node must have a unique name, and because the graph generated by GraphBuilder
    could be later a subgraph, this function compute a node name given a common non-unique
    name, the vm name, and an optional "subname" in case of record-shaped nodes.

    :param name : name of the node
    :param subname : name of the subnode (the one between <> in the record node label)
    :returns unique name of a node
    :rtype str
    '''
    def __node_name(self, name: str, subname: str = None):
        name = '{0}_{1}'.format(name, self.vm_name)
        if subname is not None:
            name += ':{0}'.format(subname)
        return name

    '''
    A record node is a node with multiple component. We use the record shape to show a
    container along with exposed ports. The container's name is at the left and the ports
    are at the right of the node, ordered top to bottom.

    In our case, the format is the following :
    { <label> text_container } { <label> text_port | <label> text_port ... }
    Then, we can address a specific subnode with the syntax global_label:label, global_label
    being the label of the record node and label being the "sublabel" (the one between <>).

    :param name : name of the container
    :param port : ports exposed by the container
    :returns label usable for the record node
    :rtype str
    '''
    def __record_label(self, name: str, ports: List[str]):
        # As the global label will already be unique, no need to use __node_name here
        # Double-bracket = single bracket in format
        label = '{{ <{0}> {0} }}'.format(name, name)
        if ports:
            label += ' | { '
            for p in ports:
                label += '<{0}> {0} |'.format(p)
            label = label[:-1] + ' }'
        return label

    '''
    Get running docker containers on the host described by docker_client, without those excluded in configuration

    :rtype List
    :returns List of ShortContainer representing running containers
    '''
    def __get_containers(self):

        # Names of all running containers
        running_containers = []

        # Get all running containers
        for c in self.docker_client.containers.list():
            # Some containers may do not have an image name for various reasons
            if c.status == 'running' and c.name not in self.exclude and len(c.image.tags) > 0:
                s = ShortContainer(c.name)
                s.image = c.image.tags[0]
                networks_conf = c.attrs['NetworkSettings']
                for expose, host in networks_conf['Ports'].items():
                    s.ports[expose].update([p['HostPort'] for p in host] if host is not None else [])
                s.url = c.labels.get('traefik.frontend.rule')
                backend_port = c.labels.get('traefik.port')
                if backend_port is not None:
                    s.backend_port = backend_port
                for n, v in networks_conf['Networks'].items():
                    s.networks.add(n)
                    if v['Links'] is not None:
                        s.links.update([l.split(':')[0] for l in v['Links']])
                running_containers.append(s)

            for i in c.image.tags:
                if 'traefik' in i.split(':')[0]:
                    self.has_traefik = True
                    self.traefik_container = c.name

        return running_containers