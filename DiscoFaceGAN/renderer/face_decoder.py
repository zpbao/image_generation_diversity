# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import tensorflow as tf 
import math as m
import numpy as np
from renderer import mesh_renderer
from scipy.io import loadmat

# Reconstruct 3D face based on output coefficients and facemodel
#-----------------------------------------------------------------------------------------

# BFM 3D face model
class BFM():
	def __init__(self,model_path = 'renderer/BFM face model/BFM_model_front_gan.mat'):
		model = loadmat(model_path)
		self.meanshape = tf.constant(model['meanshape']) # mean face shape. [3*N,1]
		self.idBase = tf.constant(model['idBase']) # identity basis. [3*N,80]
		self.exBase = tf.constant(model['exBase'].astype(np.float32)) # expression basis. [3*N,64]
		self.meantex = tf.constant(model['meantex']) # mean face texture. [3*N,1] (0-255)
		self.texBase = tf.constant(model['texBase']) # texture basis. [3*N,80]
		self.point_buf = tf.constant(model['point_buf']) # face indices for each vertex that lies in. starts from 1. [N,8]
		self.face_buf = tf.constant(model['tri']) # vertex indices for each face. starts from 1. [F,3]
		self.front_mask_render = tf.squeeze(tf.constant(model['gan_mask'])) # vertex indices for small face region for rendering. starts from 1.
		self.mask_face_buf = tf.constant(model['gan_tl']) # vertex indices for each face from small face region. starts from 1. [f,3]
		self.keypoints = tf.squeeze(tf.constant(model['keypoints'])) # vertex indices for 68 landmarks. starts from 1. [68,1]

# Analytic 3D face
class Face3D():
	def __init__(self):
		facemodel = BFM()
		self.facemodel = facemodel

	# analytic 3D face reconstructions with coefficients from R-Net
	def Reconstruction_Block(self,coeff,res,batchsize,progressive=True):
		#coeff: [batchsize,257] reconstruction coefficients
		id_coeff,ex_coeff,tex_coeff,angles,translation,gamma = self.Split_coeff(coeff)
		# [batchsize,N,3] canonical face shape in BFM space
		face_shape = self.Shape_formation_block(id_coeff,ex_coeff,self.facemodel)
		# [batchsize,N,3] vertex texture (in RGB order)
		face_texture = self.Texture_formation_block(tex_coeff,self.facemodel)
		# [batchsize,3,3] rotation matrix for face shape
		rotation = self.Compute_rotation_matrix(angles)
		# [batchsize,N,3] vertex normal
		face_norm = self.Compute_norm(face_shape,self.facemodel)
		norm_r = tf.matmul(face_norm,rotation)

		# do rigid transformation for face shape using predicted rotation and translation
		face_shape_t = self.Rigid_transform_block(face_shape,rotation,translation)
		# compute 2d landmark projections 
		# landmark_p: [batchsize,68,2]	
		face_landmark_t = self.Compute_landmark(face_shape_t,self.facemodel)
		landmark_p = self.Projection_block(face_landmark_t)   # 256*256 image

		# [batchsize,N,3] vertex color (in RGB order)
		face_color = self.Illumination_block(face_texture, norm_r, gamma)

		# reconstruction images and region masks
		if progressive:
			render_imgs,img_mask = tf.cond(res<=8, lambda:self.Render_block(face_shape_t,norm_r,face_color,self.facemodel,8,64), 
				lambda:
				tf.cond(res<=16, lambda:self.Render_block(face_shape_t,norm_r,face_color,self.facemodel,16,32),
				lambda:
				tf.cond(res<=32, lambda:self.Render_block(face_shape_t,norm_r,face_color,self.facemodel,32,16),
				lambda:
				tf.cond(res<=64, lambda:self.Render_block(face_shape_t,norm_r,face_color,self.facemodel,64,8),
				lambda: 
				tf.cond(res<=128, lambda:self.Render_block(face_shape_t,norm_r,face_color,self.facemodel,128,4),
				lambda:
				self.Render_block(face_shape_t,norm_r,face_color,self.facemodel,256,4)
				 )))))
		else:
			render_imgs,img_mask = self.Render_block(face_shape_t,norm_r,face_color,self.facemodel,res,batchsize)


		render_imgs = tf.clip_by_value(render_imgs,0,255)
		render_imgs = tf.cast(render_imgs,tf.float32) 
		render_mask = tf.cast(img_mask,tf.float32) 

		return render_imgs,render_mask,landmark_p,face_shape_t


	def Get_landmark(self,coeff):
		face_shape_t = self.Get_face_shape(coeff)
		# compute 2d landmark projections 
		# landmark_p: [batchsize,68,2]	
		face_landmark_t = self.Compute_landmark(face_shape_t,self.facemodel)
		landmark_p = self.Projection_block(face_landmark_t,focal=1015.,half_image_width=112.) # 224*224 image

		return landmark_p

	def Get_face_shape(self,coeff):
		#coeff: [batchsize,257] reconstruction coefficients

		id_coeff,ex_coeff,tex_coeff,angles,translation,gamma = self.Split_coeff(coeff)
		# [batchsize,N,3] canonical face shape in BFM space
		face_shape = self.Shape_formation_block(id_coeff,ex_coeff,self.facemodel)
		# [batchsize,3,3] rotation matrix for face shape
		rotation = self.Compute_rotation_matrix(angles)

		# do rigid transformation for face shape using predicted rotation and translation
		face_shape_t = self.Rigid_transform_block(face_shape,rotation,translation)

		return face_shape_t

	def Split_coeff(self,coeff):

		id_coeff = coeff[:,:80]
		tex_coeff = coeff[:,80:160]
		ex_coeff = coeff[:,160:224]
		angles = coeff[:,224:227]
		gamma = coeff[:,227:254]
		translation = coeff[:,254:257]
		
		return id_coeff,ex_coeff,tex_coeff,angles,translation,gamma

	def Shape_formation_block(self,id_coeff,ex_coeff,facemodel):
		face_shape = tf.einsum('ij,aj->ai',facemodel.idBase,id_coeff) + \
					tf.einsum('ij,aj->ai',facemodel.exBase,ex_coeff) + facemodel.meanshape

		# reshape face shape to [batchsize,N,3]
		face_shape = tf.reshape(face_shape,[tf.shape(face_shape)[0],-1,3])
		# re-centering the face shape with mean shape
		face_shape = face_shape - tf.reshape(tf.reduce_mean(tf.reshape(facemodel.meanshape,[-1,3]),0),[1,1,3])

		return face_shape

	def Compute_norm(self,face_shape,facemodel):
		shape = face_shape
		face_id = facemodel.face_buf
		point_id = facemodel.point_buf

		# face_id and point_id index starts from 1
		face_id = tf.cast(face_id - 1,tf.int32)
		point_id = tf.cast(point_id - 1,tf.int32)

		#compute normal for each face
		v1 = tf.gather(shape,face_id[:,0], axis = 1)
		v2 = tf.gather(shape,face_id[:,1], axis = 1)
		v3 = tf.gather(shape,face_id[:,2], axis = 1)
		e1 = v1 - v2
		e2 = v2 - v3
		face_norm = tf.cross(e1,e2)

		face_norm = tf.nn.l2_normalize(face_norm, dim = 2) # normalized face_norm first
		face_norm = tf.concat([face_norm,tf.zeros([tf.shape(face_shape)[0],1,3])], axis = 1)

		#compute normal for each vertex using one-ring neighborhood
		v_norm = tf.reduce_sum(tf.gather(face_norm, point_id, axis = 1), axis = 2)
		v_norm = tf.nn.l2_normalize(v_norm, dim = 2)
		
		return v_norm

	def Texture_formation_block(self,tex_coeff,facemodel):
		face_texture = tf.einsum('ij,aj->ai',facemodel.texBase,tex_coeff) + facemodel.meantex

		# reshape face texture to [batchsize,N,3], note that texture is in RGB order
		face_texture = tf.reshape(face_texture,[tf.shape(face_texture)[0],-1,3])

		return face_texture

	def Compute_rotation_matrix(self,angles):
		n_data = tf.shape(angles)[0]

		# compute rotation matrix for X-axis, Y-axis, Z-axis respectively
		rotation_X = tf.concat([tf.ones([n_data,1]),
			tf.zeros([n_data,3]),
			tf.reshape(tf.cos(angles[:,0]),[n_data,1]),
			-tf.reshape(tf.sin(angles[:,0]),[n_data,1]),
			tf.zeros([n_data,1]),
			tf.reshape(tf.sin(angles[:,0]),[n_data,1]),
			tf.reshape(tf.cos(angles[:,0]),[n_data,1])],
			axis = 1
			)

		rotation_Y = tf.concat([tf.reshape(tf.cos(angles[:,1]),[n_data,1]),
			tf.zeros([n_data,1]),
			tf.reshape(tf.sin(angles[:,1]),[n_data,1]),
			tf.zeros([n_data,1]),
			tf.ones([n_data,1]),
			tf.zeros([n_data,1]),
			-tf.reshape(tf.sin(angles[:,1]),[n_data,1]),
			tf.zeros([n_data,1]),
			tf.reshape(tf.cos(angles[:,1]),[n_data,1])],
			axis = 1
			)

		rotation_Z = tf.concat([tf.reshape(tf.cos(angles[:,2]),[n_data,1]),
			-tf.reshape(tf.sin(angles[:,2]),[n_data,1]),
			tf.zeros([n_data,1]),
			tf.reshape(tf.sin(angles[:,2]),[n_data,1]),
			tf.reshape(tf.cos(angles[:,2]),[n_data,1]),
			tf.zeros([n_data,3]),
			tf.ones([n_data,1])],
			axis = 1
			)

		rotation_X = tf.reshape(rotation_X,[n_data,3,3])
		rotation_Y = tf.reshape(rotation_Y,[n_data,3,3])
		rotation_Z = tf.reshape(rotation_Z,[n_data,3,3])

		# R = RzRyRx
		rotation = tf.matmul(tf.matmul(rotation_Z,rotation_Y),rotation_X)

		# because our face shape is N*3, so compute the transpose of R, so that rotation shapes can be calculated as face_shape*R 
		rotation = tf.transpose(rotation, perm = [0,2,1])

		return rotation

	def Projection_block(self,face_shape,focal=1015.0*1.22,half_image_width=128.):

		# pre-defined camera focal for pespective projection
		focal = tf.constant(focal)
		# focal = tf.constant(400.0)
		focal = tf.reshape(focal,[-1,1])
		batchsize = tf.shape(face_shape)[0]
		# center = tf.constant(112.0)

		# define camera position
		# camera_pos = tf.reshape(tf.constant([0.0,0.0,10.0]),[1,1,3])
		camera_pos = tf.reshape(tf.constant([0.0,0.0,10.0]),[1,1,3])
		# camera_pos = tf.reshape(tf.constant([0.0,0.0,4.0]),[1,1,3])
		reverse_z = tf.tile(tf.reshape(tf.constant([1.0,0,0,0,1,0,0,0,-1.0]),[1,3,3]),[tf.shape(face_shape)[0],1,1])

		# compute projection matrix
		# p_matrix = tf.concat([[focal],[0.0],[center],[0.0],[focal],[center],[0.0],[0.0],[1.0]],axis = 0)
		p_matrix = tf.concat([focal*tf.ones([batchsize,1]),tf.zeros([batchsize,1]),half_image_width*tf.ones([batchsize,1]),tf.zeros([batchsize,1]),\
			focal*tf.ones([batchsize,1]),half_image_width*tf.ones([batchsize,1]),tf.zeros([batchsize,2]),tf.ones([batchsize,1])],axis = 1)
		# p_matrix = tf.tile(tf.reshape(p_matrix,[1,3,3]),[tf.shape(face_shape)[0],1,1])
		p_matrix = tf.reshape(p_matrix,[-1,3,3])

		# convert z in canonical space to the distance to camera
		face_shape = tf.matmul(face_shape,reverse_z) + camera_pos
		aug_projection = tf.matmul(face_shape,tf.transpose(p_matrix,[0,2,1]))

		# [batchsize, N,2] 2d face projection
		face_projection = aug_projection[:,:,0:2]/tf.reshape(aug_projection[:,:,2],[tf.shape(face_shape)[0],tf.shape(aug_projection)[1],1])


		return face_projection


	def Compute_landmark(self,face_shape,facemodel):

		# compute 3D landmark postitions with pre-computed 3D face shape
		keypoints_idx = facemodel.keypoints
		keypoints_idx = tf.cast(keypoints_idx - 1,tf.int32)
		face_landmark = tf.gather(face_shape,keypoints_idx,axis = 1)

		return face_landmark

	def Illumination_block(self,face_texture,norm_r,gamma):
		n_data = tf.shape(gamma)[0]
		n_point = tf.shape(norm_r)[1]
		gamma = tf.reshape(gamma,[n_data,3,9])
		# set initial lighting with an ambient lighting
		init_lit = tf.constant([0.8,0,0,0,0,0,0,0,0])
		gamma = gamma + tf.reshape(init_lit,[1,1,9])

		# compute vertex color using SH function approximation
		a0 = m.pi 
		a1 = 2*m.pi/tf.sqrt(3.0)
		a2 = 2*m.pi/tf.sqrt(8.0)
		c0 = 1/tf.sqrt(4*m.pi)
		c1 = tf.sqrt(3.0)/tf.sqrt(4*m.pi)
		c2 = 3*tf.sqrt(5.0)/tf.sqrt(12*m.pi)

		Y = tf.concat([tf.tile(tf.reshape(a0*c0,[1,1,1]),[n_data,n_point,1]),
			tf.expand_dims(-a1*c1*norm_r[:,:,1],2),
			tf.expand_dims(a1*c1*norm_r[:,:,2],2),
			tf.expand_dims(-a1*c1*norm_r[:,:,0],2),
			tf.expand_dims(a2*c2*norm_r[:,:,0]*norm_r[:,:,1],2),
			tf.expand_dims(-a2*c2*norm_r[:,:,1]*norm_r[:,:,2],2),
			tf.expand_dims(a2*c2*0.5/tf.sqrt(3.0)*(3*tf.square(norm_r[:,:,2])-1),2),
			tf.expand_dims(-a2*c2*norm_r[:,:,0]*norm_r[:,:,2],2),
			tf.expand_dims(a2*c2*0.5*(tf.square(norm_r[:,:,0])-tf.square(norm_r[:,:,1])),2)],axis = 2)

		color_r = tf.squeeze(tf.matmul(Y,tf.expand_dims(gamma[:,0,:],2)),axis = 2)
		color_g = tf.squeeze(tf.matmul(Y,tf.expand_dims(gamma[:,1,:],2)),axis = 2)
		color_b = tf.squeeze(tf.matmul(Y,tf.expand_dims(gamma[:,2,:],2)),axis = 2)

		#[batchsize,N,3] vertex color in RGB order
		face_color = tf.stack([color_r*face_texture[:,:,0],color_g*face_texture[:,:,1],color_b*face_texture[:,:,2]],axis = 2)

		return face_color

	def Rigid_transform_block(self,face_shape,rotation,translation):
		# do rigid transformation for 3D face shape
		face_shape_r = tf.matmul(face_shape,rotation)
		face_shape_t = face_shape_r + tf.reshape(translation,[tf.shape(face_shape)[0],1,3])

		return face_shape_t

	def Render_block(self,face_shape,face_norm,face_color,facemodel,res,batchsize):
		# render reconstruction images 
		n_vex = int(facemodel.idBase.shape[0].value/3)
		fov_y = 2*tf.atan(128/(1015.*1.22))*180./m.pi
		# full face region
		face_shape = tf.reshape(face_shape,[batchsize,n_vex,3])
		face_norm = tf.reshape(face_norm,[batchsize,n_vex,3])
		face_color = tf.reshape(face_color,[batchsize,n_vex,3])

		# pre-defined cropped face region
		mask_face_shape = tf.gather(face_shape,tf.cast(facemodel.front_mask_render-1,tf.int32),axis = 1)
		mask_face_norm = tf.gather(face_norm,tf.cast(facemodel.front_mask_render-1,tf.int32),axis = 1)
		mask_face_color = tf.gather(face_color,tf.cast(facemodel.front_mask_render-1,tf.int32),axis = 1)

		# setting cammera settings
		camera_position = tf.constant([[0,0,10.0]]) + tf.zeros([batchsize,3])
		camera_lookat = tf.constant([[0,0,0.0]]) + tf.zeros([batchsize,3])
		camera_up = tf.constant([[0,1.0,0]]) + tf.zeros([batchsize,3])

		# setting light source position(intensities are set to 0 because we have computed the vertex color)
		light_positions = tf.reshape(tf.constant([0,0,1e5]),[1,1,3]) + tf.zeros([batchsize,1,3])
		light_intensities = tf.reshape(tf.constant([0.0,0.0,0.0]),[1,1,3])+tf.zeros([batchsize,1,3])
		ambient_color = tf.reshape(tf.constant([1.0,1,1]),[1,3])+ tf.zeros([batchsize,3])

		near_clip = 0.01
		far_clip = 50.

		# using tf_mesh_renderer for rasterization, 
		# https://github.com/google/tf_mesh_renderer
		
		# img: [batchsize,224,224,3] images in RGB order (0-255)
		# mask:[batchsize,224,224,1] transparency for img ({0,1} value)
		with tf.device('/cpu:0'):
			rgba_img = mesh_renderer.mesh_renderer(mask_face_shape,
				tf.cast(facemodel.mask_face_buf-1,tf.int32),
				mask_face_norm,
				mask_face_color,
				camera_position = camera_position,
				camera_lookat = camera_lookat,
				camera_up = camera_up,
				light_positions = light_positions,
				light_intensities = light_intensities,
				image_width = res,
				image_height = res,
				# fov_y = 12.5936,
				fov_y = fov_y,
				ambient_color = ambient_color,
				near_clip = near_clip,
				far_clip = far_clip)

		img = rgba_img[:,:,:,:3]
		mask = rgba_img[:,:,:,3:]

		return img,mask


