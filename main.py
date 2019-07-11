#!/usr/bin/env python
# coding=utf-8

import docker
import graphviz
import json
import socket
import os
from ruamel.yaml import YAML
from collections import defaultdict

class ShortContainer:
	'''
	This class represents a Docker container with only useful members
	for GraphBot.
	'''

	def __init__(self, name):
		self.name = name;
		self.image = str()
		self.ports = defaultdict(set)
		self.url = str()
		self.networks = set()
		self.links = set()

	@property
	def url(self):
		return self.__name;

	@url.setter
	def url(self, value):
		if value is not None:
			value = value.replace('Host:', '')
		self.__name = value

class GraphBot:
	'''
	This class asks the Docker daemon informations about running Containers
	and constructs a graph showing dependencies between containers.

	We also use Traefik labels to show links between the reverse proxy and the containers.
	'''

	DIR_NAME = os.path.dirname(os.path.realpath(__file__))

	def __init__(self, config_path = DIR_NAME, output_dir = DIR_NAME):
		# Get configuration
		with open(os.path.join(config_path, 'config.json'), 'r') as fd:
			self.config = json.load(fd)
		self.output_dir = os.path.join(config_path, 'output')
		self.docker_client = docker.from_env()
		self.has_traefik = False

	def build_graph(self):
		running = self.__get_containers()
		g = graphviz.Digraph(comment = 'Machine physique : {0}'.format(self.config['machine_name']), format = 'png')
		g.attr(label = 'Machine physique : {0}'.format(self.config['machine_name']))

		# Create a subgraph for the virtual machine
		with g.subgraph(name = 'cluster_0') as vm:
			vm.attr(label = 'Machine virtuelle : {0}'.format(socket.gethostname()))

			# Group containers by networks
			network_dict = defaultdict(list)
			for c in running:
				for n in c.networks:
					network_dict[n].append(c)

			# Add all running containers as a node in their own network subgraph
			for k, v in network_dict.items():
				with vm.subgraph(name = 'cluster_{0}'.format(k)) as cluster:
					cluster.attr(label = 'Réseau : {0}'.format(k))
					for c in v:
						with cluster.subgraph(name = 'cluster_{0}'.format(c.image)) as image:
							image.attr(label = 'Image : {0}'.format(c.image), style = "filled,rounded")
							label = '{' + c.name + '}|{'
							for p in c.ports:
								label += '<{0}> {0}|'.format(p)
							label = label[:-1] + '}'
							image.node(c.name, label, shape = "record", style = "filled,rounded", fillcolor = "white")

			for c in running:
				# Add reverse-proxy links
				if self.has_traefik:
					vm.edge(self.traefik_container, c.name, label = c.url, style = "dotted")

				# Add links
				vm.edges([(c.name, l) for l in c.links])

				# Add port mapping
				for expose, host_ports in c.ports.items():
					vm.edges([(host, '{0}:{1}'.format(c.name, expose)) for host in host_ports])

		# Render PNG
		g.render(os.path.join(self.output_dir, '{0}.gv'.format(socket.gethostname())))

	def __get_containers(self):
		'''
		Get running docker containers on the host described by docker_client, without those excluded in configuration

		:rtype List
		:returns List of ShortContainer representing running containers
		'''

		# Names of all running containers
		running_containers = []

		# Get all running containers
		for c in self.docker_client.containers.list():
			if c.status == 'running' and c.name not in self.config['exclude']:
				s = ShortContainer(c.name)
				s.image = c.image.tags[0]
				networks_conf = c.attrs['NetworkSettings']
				for expose, host in networks_conf['Ports'].items():
					s.ports[expose].update([p['HostPort'] for p in host] if host is not None else [])
				s.url = c.labels.get('traefik.frontend.rule')
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

def main():
	graph = GraphBot()
	graph.build_graph()

if __name__ == '__main__':
    main()
