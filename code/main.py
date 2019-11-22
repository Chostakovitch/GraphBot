#!/usr/bin/env python
#coding=utf-8

import docker
import graphviz
import json
import socket
import os
import dns.resolver

from ruamel.yaml import YAML
from collections import defaultdict
from enum import Enum
from jsonschema import validate
from urllib.request import urlopen
from datetime import datetime

TRAEFIK_DEFAULT_PORT = '80/tcp'
BASE_PATH = os.environ['DATA_PATH']
OUTPUT_PATH = os.environ['OUTPUT_PATH']

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
    def __init__(self, name):
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
This class asks the Docker daemon informations about running Containers
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

    :param docker_client (DockerClient): docker client used to build the graph
    :param color_scheme (dict): colors used for the graph
    :param vm_name (str): name of the virtual machine
    :param vm_label (str): label to put on the virtual machine graph
    :param exclude (list): name of containers to exclude of the layout
    '''
    def __init__(self, docker_client, color_scheme, vm_label, vm_name, exclude = []):
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

    :param node_type(GraphElement) Which part of the graph do we need to style
    :returns Dictionary containing the styling arguments
    :rtype Dict(str, str)
    '''
    def __get_style(self, graph_element):
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

    :param name(str): name of the node
    :param subname(str): name of the subnode (the one between <> in the record node label)
    :returns unique name of a node
    :rtype str
    '''
    def __node_name(self, name, subname = None):
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

    :param name (str): name of the container
    :param port (List(str)): ports exposed by the container
    :returns label usable for the record node
    :rtype str
    '''
    def __record_label(self, name, ports):
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

'''
This class is used to create a graph per machine given in the configuration,
and then combines those graphs to create the "big-picture" graph.

This graph can then be pushed to a cloud or a Git repository.
'''
class GraphBot:
    @property
    def graph(self):
        if self.__graph is None:
            self.build()
        return self.__graph

    @property
    def legend(self):
        if self.__legend is None:
            self.__legend = graphviz.Digraph('legend', node_attr = { 'style': 'rounded', 'shape': 'plain' }, format = 'png')
            self.__legend.node('legend', '''<
            <TABLE BORDER="1" CELLBORDER="0" CELLSPACING="10" CELLPADDING="4">
                <TR>
                    <TD COLSPAN="2"><B>Legend of {0} architecture</B></TD>
                </TR>
                <TR>
                    <TD ALIGN="LEFT">Traefik "Host" label</TD>
                    <TD BORDER="1" WIDTH="100" BGCOLOR="{1}"></TD>
                </TR>
                <TR>
                    <TD ALIGN="LEFT">Host port</TD>
                    <TD BORDER="1" WIDTH="100" BGCOLOR="{2}"></TD>
                </TR>
                <TR>
                    <TD ALIGN="LEFT">Docker link</TD>
                    <TD BORDER="1" WIDTH="100" BGCOLOR="{3}"></TD>
                </TR>
                <TR>
                    <TD ALIGN="LEFT">Image</TD>
                    <TD BORDER="1" WIDTH="100" BGCOLOR="{4}"></TD>
                </TR>
                <TR>
                    <TD ALIGN="LEFT">Container, exposed ports</TD>
                    <TD BORDER="1" WIDTH="100" BGCOLOR="{5}"></TD>
                </TR>
                <TR>
                    <TD ALIGN="LEFT">Docker network</TD>
                    <TD BORDER="1" WIDTH="100" BGCOLOR="{6}"></TD>
                </TR>
                <TR>
                    <TD ALIGN="LEFT">Virtual machine</TD>
                    <TD BORDER="1" WIDTH="100" BGCOLOR="{7}"></TD>
                </TR>
            </TABLE>>'''.format(
                self.config['organization'],
                self.config['color_scheme']['traefik'],
                self.config['color_scheme']['port'],
                self.config['color_scheme']['link'],
                self.config['color_scheme']['image'],
                self.config['color_scheme']['container'],
                self.config['color_scheme']['network'],
                self.config['color_scheme']['vm']
            ))
        return self.__legend

    def __init__(self, config_path = os.path.join(BASE_PATH, 'config.json')):
        with open(config_path) as fd:
            self.config = json.load(fd)

        # Validate configuration
        self.__check_config()

        self.__graph = None
        self.__legend = None
        self.__generated_files = []

    '''
    Builds a Digraph object representing the architecture of all hosts.
    After running this function, the __graph attribute contains the final graph.
    '''
    def build(self):
        graph_attr = {
            # Draw straight lines
            'splines': 'false',
            # Merge edges when possible
            'concentrate': 'true',
            # Minimum distance (inches) between node of successive ranks
            'ranksep': '0.8 equally',
            # Allow edges between clusters (important for our invisible edges between subgraphs)
            'compound': 'true',
            # Defaut text/border color
            'fontcolor': self.config['color_scheme']['dark_text']
        }
        node_attr = {
            # All nodes are colorfull and with rounded borders
            'style': 'filled,rounded',
            # Allow sub-nodes
            'shape': 'record'
        }
        graph_name = '{} architecture'.format(self.config['organization'])
        self.__graph = graphviz.Digraph(
            name = graph_name,
            comment = graph_name,
            graph_attr = graph_attr,
            node_attr = node_attr,
            format = 'png'
        )

        graphs = self.__build_subgraphs()
        self.__render_graph(graphs)

        for a in self.config['actions']:
            if type == 'webdav':
                self.__upload_to_webdav()

    '''
    Upload the generated PNG in self.__generated_files to a WebDAV compatible server.
    '''
    def __upload_to_webdav(self):
        return

    '''
    Render one or several graphs in PNG format from a list of graphs
    :param graphs (list): GraphBuilder objects holding the graphs
    '''
    def __render_graph(self, graphs):
        for builder in graphs:
            if self.config['merge']:
                self.__graph.subgraph(graph = builder.graph)
            else:
                self.__graph.body = builder.graph.body
                path = os.path.join(OUTPUT_PATH, builder.vm_name)
                self.__graph.render(path)
                self.__generated_files.append(path)

        if self.config['merge']:
            path = os.path.join(OUTPUT_PATH, self.config['organization'])
            self.__graph.render(path)
            self.__generated_files.append(path)
            print("Global rendering is successful !")

        self.legend.render(os.path.join(OUTPUT_PATH, 'legend'))
        print("Legend rendering is successful !")

    '''
    Query all hosts and return all corresponding graphs
    :returns Graphs of hosts
    :rtype List(GraphBuilder)
    '''
    def __build_subgraphs(self):
        graphs = []
        for host in self.config['hosts']:
            vm_name = host['vm'] + ' | '
            if host['host_url'] == 'localhost':
                docker_client = docker.from_env()
                vm_name += urlopen('http://ip.42.pl/raw').read().decode("utf-8")
            else:
                tls_config = docker.tls.TLSConfig(
                    client_cert = (
                        os.path.join(BASE_PATH, host['tls_config']['cert']),
                        os.path.join(BASE_PATH, host['tls_config']['key'])
                    ),
                    verify = os.path.join(BASE_PATH, host['tls_config']['ca_cert'])
                )
                docker_client = docker.DockerClient(base_url = '{0}:{1}'.format(host['host_url'], host['port']), tls = tls_config)
                for result in dns.resolver.query(host['host_url']):
                    vm_name += '{}'.format(result.address)
            vm_name += ' | Generated date : {} '.format(datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
            builder = GraphBuilder(docker_client, self.config['color_scheme'], vm_name, host['vm'], host.get('exclude', []))
            print('{} built.'.format(builder.graph.name))
            graphs.append(builder)

        return graphs

    '''
    Perform syntaxic and logic checks of the configuration.
    :returns None if the configuration is clean, an informative error message otherwise
    :rtype str
    '''
    def __check_config(self):
        with open('schema.json') as schema:
            try:
                validate(self.config, json.load(schema))
            except Exception as valid_err:
                raise Exception("Invalid configuration: {}".format(valid_err))

        hosts = [host['vm'] for host in self.config['hosts']]
        unique_hosts = set(hosts)
        if len(hosts) != len(unique_hosts):
            duplicate = [h for h in hosts if not h in unique_hosts or unique_hosts.remove(h)]
            raise Exception('Invalid configuration: two hosts cannot have the same name ({})'.format(duplicate[0]))

if __name__ == '__main__':
    bot = GraphBot()
    bot.build()
