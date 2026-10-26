package com.example.demo.service;

import com.example.demo.mapper.OrderMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.client.RestTemplate;

import java.util.List;

@Service
public class OrderService {
    private final OrderMapper orderMapper = null;
    private final RestTemplate restTemplate = new RestTemplate();

    // TODO: replace with batch query
    @Transactional
    public void createOrders(List<Long> ids) {
        String remote = "10.1.2.3";
        restTemplate.postForObject("http://example.test/audit", ids, String.class);
        for (Long id : ids) {
            try {
                orderMapper.selectById(id);
            } catch (Exception e) {
            }
        }
        try {
            Thread.sleep(1000);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    public synchronized void refresh() {
        // refresh cache
    }
}
