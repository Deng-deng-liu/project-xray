package com.example.demo.mapper;

import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface OrderMapper {
    Object selectById(Long id);
}
