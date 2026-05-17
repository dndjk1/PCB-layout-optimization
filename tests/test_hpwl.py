from src.hpwl import net_hpwl_detail, total_hpwl
from src.pcb_data import Component, Net, Pin, Placement


def test_total_hpwl_for_one_net():
    components = {
        "A": Component("A", 10, 10),
        "B": Component("B", 10, 10),
    }
    placements = {
        "A": Placement("A", 0, 0, "N"),
        "B": Placement("B", 20, 30, "N"),
    }
    nets = [Net("N1", [Pin("A", 0, 0), Pin("B", 0, 0)])]

    assert total_hpwl(nets, components, placements) == 50


def test_net_hpwl_detail_keeps_bounding_box():
    components = {
        "A": Component("A", 10, 10),
        "B": Component("B", 20, 10),
        "C": Component("C", 10, 20),
    }
    placements = {
        "A": Placement("A", 0, 0, "N"),
        "B": Placement("B", 100, 20, "N"),
        "C": Placement("C", 40, 80, "N"),
    }
    net = Net("N1", [Pin("A", 0, 0), Pin("B", -10, 0), Pin("C", 0, -10)])

    detail = net_hpwl_detail(net, components, placements)

    assert detail.degree == 3
    assert detail.min_x == 5
    assert detail.max_x == 100
    assert detail.min_y == 5
    assert detail.max_y == 80
    assert detail.hpwl == 170
